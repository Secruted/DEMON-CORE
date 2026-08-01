import requests
import urllib3
import concurrent.futures

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def verify_elite(proxy):
    url = "https://gist.github.com" # Target-specific test
    proxy_dict = {'http': f'socks5h://{proxy}', 'https': f'socks5h://{proxy}'}
    try:
        # Testing if the proxy can actually reach GitHub under 8 seconds
        r = requests.get(url, proxies=proxy_dict, timeout=8, verify=False)
        if r.status_code == 200:
            return proxy
    except:
        return None

def main():
    with open('proxy.txt', 'r') as f:
        proxies = [l.strip() for l in f if l.strip()]
    
    print(f"[*] Verifying {len(proxies)} survivors against GitHub...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
        results = list(executor.map(verify_elite, proxies))
        elite = [p for p in results if p is not None]

    with open('proxy_elite.txt', 'w') as f:
        for p in elite:
            f.write(p + '\n')
    
    print(f"[+] Done. Elite proxies found: {len(elite)}")
main()
