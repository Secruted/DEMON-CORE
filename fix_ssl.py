import os

files = ['content_parser.py', 'proxy_manager.py']
patch = """
import urllib3
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
original_get = requests.get
requests.get = lambda *args, **kwargs: original_get(*args, **{**kwargs, 'verify': False})
"""

for f_name in files:
    if os.path.exists(f_name):
        with open(f_name, 'r') as f:
            content = f.read()
        if 'InsecureRequestWarning' not in content:
            with open(f_name, 'w') as f:
                f.write(patch + "\n" + content)
            print(f"[+] Patched: {f_name}")
