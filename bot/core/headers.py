from typing import Dict

HEADERS = {
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'ru,en-US;q=0.9,en;q=0.8',
    'cache-control': 'no-cache',
    'content-type': 'application/json',
    'dnt': '1',
    'origin': 'https://www.yescoin.gold',
    'pragma': 'no-cache',
    'priority': 'u=1, i',
    'referer': 'https://www.yescoin.gold/',
    'sec-ch-ua': '"Chromium";v="131", "Not_A Brand";v="24"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"macOS"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-site',
    'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
}

def get_auth_headers(token: str) -> Dict[str, str]:
    auth_headers = HEADERS.copy()
    auth_headers['authorization'] = f'Bearer {token}'
    auth_headers['lang'] = 'en'
    return auth_headers
