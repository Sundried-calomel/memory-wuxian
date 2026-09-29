"""Install a real published package in isolation and load its entry points once."""
import argparse
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'installer'))
from package import read_package
from transaction import Installer
from platform_runtime import PlatformRuntime

parser=argparse.ArgumentParser()
parser.add_argument('--package',required=True)
parser.add_argument('--sha256',required=True)
args=parser.parse_args()
package=read_package(args.package,args.sha256)
with tempfile.TemporaryDirectory() as directory:
    installer=Installer(Path(directory).resolve()/'product')
    assert installer.apply(package,PlatformRuntime(offline=True))['status']=='applied'
    assert installer.apply(package,PlatformRuntime(offline=True))['status']=='unchanged'
print('Published package installed; entry imports and native helper loaded; repeat changed zero files.')
