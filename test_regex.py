import re

EMOJI_PATTERN = re.compile(
    r'['
    r'\U0001f300-\U0001f64f'  # Miscellaneous Symbols and Pictographs
    r'\U0001f680-\U0001f6ff'  # Transport and Map Symbols
    r'\U0001f900-\U0001f9ff'  # Supplemental Symbols and Pictographs
    r'\U0001fa70-\U0001faff'  # Symbols and Pictographs Extended-A
    r'\u2600-\u26ff'          # Miscellaneous Symbols
    r'\u2700-\u27bf'          # Dingbats
    r']'
)

ASIAN_SCRIPTS_PATTERN = re.compile(
    r'['
    r'\u0900-\u097f'  # Devanagari (Hindi)
    r'\u0980-\u09ff'  # Bengali
    r'\u1780-\u17ff\u19e0-\u19ff'  # Khmer
    r'\u1000-\u109f\uaa60-\uaa7f\ua9e0-\ua9ff'  # Burmese (Myanmar)
    r']'
)

print(bool(EMOJI_PATTERN.search('test😀')))
print(bool(EMOJI_PATTERN.search('test')))
print(bool(ASIAN_SCRIPTS_PATTERN.search('नमस्ते'))) # Hindi
print(bool(ASIAN_SCRIPTS_PATTERN.search('বাংলা'))) # Bengali
print(bool(ASIAN_SCRIPTS_PATTERN.search('ខ្មែរ'))) # Khmer
print(bool(ASIAN_SCRIPTS_PATTERN.search('မြန်မာ'))) # Burmese
print(bool(ASIAN_SCRIPTS_PATTERN.search('中文'))) # Chinese (should be False)

