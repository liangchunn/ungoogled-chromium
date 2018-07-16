#!/bin/bash

# ./developer_utilities/validate_config.py
# ./developer_utilities/pylint_buildkit.py --hide-fixme
# ./developer_utilities/pylint_devutils.py --hide-fixme developer_utilities/
mkdir -p buildspace/downloads
./buildkit-launcher.py genbun macos
./buildkit-launcher.py getsrc
./buildkit-launcher.py subdom
./buildkit-launcher.py genpkg macos
cd buildspace/tree
chmod +x ungoogled_packaging/build.sh
touch BUILD_IN_PROGRESS