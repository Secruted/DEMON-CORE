import os

# Files that handle network requests
target_files = ['harvester.py', 'content_parser.py', 'orchestrator.py']

bypass_logic = """
import urllib3
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
urllib3.disable_warnings(InsecureRequestWarning)
# Global Force Bypass
original_get = requests.Session.get
def patched_get(self, url, **kwargs):
    kwargs['verify'] = False
    return original_get(self, url, **kwargs)
requests.Session.get = patched_get
"""

for f_name in target_files:
    if os.path.exists(f_name):
        with open(f_name, 'r') as f:
            content = f.read()
        if 'InsecureRequestWarning' not in content:
            with open(f_name, 'w') as f:
                f.write(bypass_logic + "\n" + content)
            print(f"[+] SSL Bypass Injected: {f_name}")
