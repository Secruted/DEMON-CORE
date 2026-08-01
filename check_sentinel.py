import requests
import urllib3

# Disable SSL warnings for the check
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def check_proxies(file_path='proxy.txt'):
    print(f"[*] Starting Proxy Sentinel Check on: {file_path}")
    try:
        with open(file_path, 'r') as f:
            proxies = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print("[-] Error: proxy.txt not found!")
        return

    success_count = 0
    for p in proxies[:10]:  # Test first 10 for speed
        proxy_url = f"socks5h://{p}"
        try:
            # Testing against a neutral IP service
            response = requests.get('https://api.ipify.org?format=json', 
                                    proxies={'http': proxy_url, 'https': proxy_url}, 
                                    timeout=10, 
                                    verify=False)
            if response.status_code == 200:
                print(f"[+] Proxy {p} is ALIVE. Detected IP: {response.json()['ip']}")
                success_count += 1
            else:
                print(f"[-] Proxy {p} returned status: {response.status_code}")
        except Exception as e:
            print(f"[-] Proxy {p} FAILED. Error: {str(e)[:50]}")

    print(f"\n[!] Check Complete. {success_count}/10 proxies tested are working.")

if __name__ == "__main__":
    check_proxies()
