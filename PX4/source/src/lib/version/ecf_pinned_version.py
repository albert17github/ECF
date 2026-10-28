"""Generate the native version macros from the checked source snapshot metadata."""
import json
from pathlib import Path
import re
import sys

metadata = json.loads(Path(sys.argv[1]).read_text())
assert metadata['project'] == 'PX4' and metadata['tag'] == 'v1.17.0'
commit = metadata['commit']
assert re.fullmatch(r'[0-9a-f]{40}', commit)
dependencies = metadata['dependencies']
values = {
    'PX4_GIT_VERSION_STR': commit,
    'PX4_GIT_TAG_STR': metadata['tag'],
    'PX4_GIT_BRANCH_NAME': 'ecf-r' + str(metadata['ecf_revision']),
    'PX4_GIT_OEM_VERSION_STR': 'ECF-r' + str(metadata['ecf_revision']),
    'PX4_GIT_TAG_OR_BRANCH_NAME': metadata['tag'],
    'MAVLINK_LIB_GIT_VERSION_STR': dependencies['src/modules/mavlink/mavlink'],
    'NUTTX_GIT_VERSION_STR': dependencies['platforms/nuttx/NuttX/nuttx'],
    'NUTTX_GIT_TAG_STR': metadata['nuttx_version_tag'],
}
header = '#pragma once\n'
for key, value in values.items():
    header += '#define ' + key + ' ' + json.dumps(value) + '\n'
for key, value in [('PX4_GIT_VERSION_BINARY', commit),
                   ('MAVLINK_LIB_GIT_VERSION_BINARY', dependencies['src/modules/mavlink/mavlink']),
                   ('NUTTX_GIT_VERSION_BINARY', dependencies['platforms/nuttx/NuttX/nuttx'])]:
    assert re.fullmatch(r'[0-9a-f]{40}', value)
    header += '#define ' + key + ' 0x' + value[:16] + '\n'
output = Path(sys.argv[2])
if not output.exists() or output.read_text() != header:
    output.write_text(header)
