import json
import xml.etree.ElementTree as ET
import sys
import os
from pathlib import Path
from collections import defaultdict, Counter

import langcodes
from langcodes.util import data_filename
from langcodes.language_lists import CLDR_LANGUAGES
from langcodes.registry_parser import parse_registry


def read_cldr_supplemental(dataname):
    cldr_supp_path = data_filename('cldr-json/cldr-json/cldr-core/supplemental')
    filename = data_filename(f'{cldr_supp_path}/{dataname}.json')
    fulldata = json.load(open(filename, encoding='utf-8'))
    if dataname == 'aliases':
        data = fulldata['supplemental']['metadata']['alias']
    else:
        data = fulldata['supplemental'][dataname]
    return data


def read_iana_registry_scripts():
    scripts = {}
    for entry in parse_registry():
        if entry['Type'] == 'language' and 'Suppress-Script' in entry:
            scripts[entry['Subtag']] = entry['Suppress-Script']
    return scripts


def read_iana_registry_macrolanguages():
    macros = {}
    for entry in parse_registry():
        if entry['Type'] == 'language' and 'Macrolanguage' in entry:
            macros[entry['Subtag']] = entry['Macrolanguage']
    return macros


def read_iana_registry_replacements():
    replacements = {}
    for entry in parse_registry():
        if entry['Type'] == 'language' and 'Preferred-Value' in entry:
            # Replacements for language codes
            replacements[entry['Subtag']] = entry['Preferred-Value']
        elif 'Tag' in entry and 'Preferred-Value' in entry:
            # Replacements for entire tags
            replacements[entry['Tag'].lower()] = entry['Preferred-Value']
    return replacements


def write_python_dict(outfile, name, d):
    print(f"{name} = {{", file=outfile)
    for key in sorted(d):
        value = d[key]
        print(f"    {key!r}: {value!r},", file=outfile)
    print("}", file=outfile)


def write_python_set(outfile, name, s):
    print(f"{name} = {{", file=outfile)
    for key in sorted(set(s)):
        print(f"    {key!r},", file=outfile)
    print("}", file=outfile)


GENERATED_HEADER = "# This file is generated by build_data.py."


def read_validity_regex():
    validity_options = []
    for codetype in ('language', 'region', 'script', 'variant'):
        validity_path = data_filename(f'cldr/common/validity/{codetype}.xml')
        root = ET.fromstring(open(validity_path).read())
        matches = root.findall('./idValidity/id')
        for match in matches:
            for item in match.text.strip().split():
                if '~' in item:
                    assert item[-2] == '~'
                    prefix = item[:-3]
                    range_start = item[-3]
                    range_end = item[-1]
                    option = f"{prefix}[{range_start}-{range_end}]"
                    validity_options.append(option)
                else:
                    validity_options.append(item)
    options = '|'.join(validity_options)
    return f'^({options})$'


def read_language_distances():
    language_info_path = data_filename('cldr/common/supplemental/languageInfo.xml')
    root = ET.fromstring(open(language_info_path).read())
    matches = root.findall(
        './languageMatching/languageMatches[@type="written_new"]/languageMatch'
    )
    tag_distances = {}
    for match in matches:
        attribs = match.attrib
        n_parts = attribs['desired'].count('_') + 1
        if n_parts < 3:
            if attribs.get('oneway') == 'true':
                pairs = [(attribs['desired'], attribs['supported'])]
            else:
                pairs = [
                    (attribs['desired'], attribs['supported']),
                    (attribs['supported'], attribs['desired']),
                ]
            for (desired, supported) in pairs:
                desired_distance = tag_distances.setdefault(desired, {})
                desired_distance[supported] = int(attribs['distance'])

                # The 'languageInfo' data file contains distances for the unnormalized
                # tag 'sh', but we work mostly with normalized tags, and they don't
                # describe at all how to cope with this.
                #
                # 'sh' normalizes to 'sr-Latn', and when we're matching languages we
                # aren't matching scripts yet, so when 'sh' appears we'll add a
                # corresponding match for 'sr'.
                #
                # Then because we're kind of making this plan up, add 1 to the distance
                # so it's a worse match than ones that are actually clearly defined
                # in languageInfo.
                if desired == 'sh' or supported == 'sh':
                    if desired == 'sh':
                        desired = 'sr'
                    if supported == 'sh':
                        supported = 'sr'
                    if desired != supported:
                        # don't try to define a non-zero distance for sr <=> sr
                        desired_distance = tag_distances.setdefault(desired, {})
                        desired_distance[supported] = int(attribs['distance']) + 1

    return tag_distances


def build_data():
    lang_scripts = read_iana_registry_scripts()
    macrolanguages = read_iana_registry_macrolanguages()
    iana_replacements = read_iana_registry_replacements()
    language_distances = read_language_distances()

    alias_data = read_cldr_supplemental('aliases')
    likely_subtags = read_cldr_supplemental('likelySubtags')
    replacements = {}

    # Aliased codes can still have alpha3 codes, and there's no unified source
    # about what they are. It depends on whether the alias predates or postdates
    # ISO 639-2, which nobody should have to care about. So let's set all the
    # alpha3 codes for aliased alpha2 codes here.
    alpha3_mapping = {
        'tl': 'tgl',  # even though it normalizes to 'fil'
        'in': 'ind',
        'iw': 'heb',
        'ji': 'yid',
        'jw': 'jav',
        'sh': 'hbs',
    }
    alpha3_biblio = {}
    norm_macrolanguages = {}
    for alias_type in ['languageAlias', 'scriptAlias', 'territoryAlias']:
        aliases = alias_data[alias_type]
        # Initially populate 'languageAlias' with the aliases from the IANA file
        if alias_type == 'languageAlias':
            replacements[alias_type] = iana_replacements
            replacements[alias_type]['root'] = 'und'
        else:
            replacements[alias_type] = {}
        for code, value in aliases.items():
            # Make all keys lowercase so they can be looked up
            # case-insensitively
            code = code.lower()

            # If there are multiple replacements, take the first one. For example,
            # we just replace the Soviet Union (SU) with Russia (RU), instead of
            # trying to do something context-sensitive and poorly standardized
            # that selects one of the successor countries to the Soviet Union.
            replacement = value['_replacement'].split()[0]
            if value['_reason'] == 'macrolanguage':
                norm_macrolanguages[code] = replacement
            else:
                # CLDR tries to oversimplify some codes as it assigns aliases.
                # For example, 'nor' is the ISO alpha3 code for 'no', but CLDR
                # would prefer you use 'nb' over 'no', so it makes 'nor' an
                # alias of 'nb'. But 'nb' already has an alpha3 code, 'nob'.
                #
                # We undo this oversimplification so that we can get a
                # canonical mapping between alpha2 and alpha3 codes.
                if code == 'nor':
                    replacement = 'no'
                elif code == 'mol':
                    replacement = 'mo'
                elif code == 'twi':
                    replacement = 'tw'
                elif code == 'bih':
                    replacement = 'bh'

                replacements[alias_type][code] = replacement
                if alias_type == 'languageAlias':
                    if value['_reason'] == 'overlong':
                        if replacement in alpha3_mapping:
                            raise ValueError(
                                "{code!r} is an alpha3 for {replacement!r}, which"
                                " already has an alpha3: {orig!r}".format(
                                    code=code,
                                    replacement=replacement,
                                    orig=alpha3_mapping[replacement],
                                )
                            )
                        alpha3_mapping[replacement] = code
                    elif value['_reason'] == 'bibliographic':
                        alpha3_biblio[replacement] = code

    validity_regex = read_validity_regex()

    # Write the contents of data_dicts.py.
    with open('data_dicts.py', 'w', encoding='utf-8') as outfile:
        print(GENERATED_HEADER, file=outfile)
        print("import re\n", file=outfile)
        write_python_dict(outfile, 'DEFAULT_SCRIPTS', lang_scripts)
        write_python_dict(
            outfile, 'LANGUAGE_REPLACEMENTS', replacements['languageAlias']
        )
        write_python_dict(outfile, 'LANGUAGE_ALPHA3', alpha3_mapping)
        write_python_dict(outfile, 'LANGUAGE_ALPHA3_BIBLIOGRAPHIC', alpha3_biblio)
        write_python_dict(outfile, 'SCRIPT_REPLACEMENTS', replacements['scriptAlias'])
        write_python_dict(
            outfile, 'TERRITORY_REPLACEMENTS', replacements['territoryAlias']
        )
        write_python_dict(outfile, 'MACROLANGUAGES', macrolanguages)
        write_python_dict(outfile, 'NORMALIZED_MACROLANGUAGES', norm_macrolanguages)
        write_python_dict(outfile, 'LIKELY_SUBTAGS', likely_subtags)
        write_python_dict(outfile, 'LANGUAGE_DISTANCES', language_distances)
        print(f"VALIDITY = re.compile({validity_regex!r})", file=outfile)


if __name__ == '__main__':
    build_data()
