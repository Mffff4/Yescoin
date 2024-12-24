from typing import Dict, Optional, Any
import json
import os
from pathlib import Path

from bot.utils.ton import generate_wallet
from bot.utils import logger


def load_wallet_config(config_path: str) -> Dict[str, Any]:
    wallet_config_path = Path(config_path).parent / 'wallet_config.json'
    if not wallet_config_path.exists():
        with open(wallet_config_path, 'w') as f:
            json.dump({}, f, indent=4)
        return {}
    
    with open(wallet_config_path, 'r') as f:
        return json.load(f)


def save_wallet_config(config_path: str, wallet_data: Dict[str, Any]) -> None:
    wallet_config_path = Path(config_path).parent / 'wallet_config.json'
    
    with open(wallet_config_path, 'w') as f:
        json.dump(wallet_data, f, indent=4)


def get_wallet_data(config_path: str, session_name: str) -> Optional[Dict[str, Any]]:
    wallet_config = load_wallet_config(config_path)
    return wallet_config.get(session_name)


def update_accounts_config_wallet(config_path: str, session_name: str, wallet_address: str) -> None:
    accounts_config_path = Path(config_path).parent / 'accounts_config.json'
    if not accounts_config_path.exists():
        return

    with open(accounts_config_path, 'r') as f:
        accounts_config = json.load(f)

    if session_name in accounts_config:
        if 'ton_address' not in accounts_config[session_name]:
            accounts_config[session_name]['ton_address'] = wallet_address
            with open(accounts_config_path, 'w') as f:
                json.dump(accounts_config, f, indent=4)


def create_and_save_wallet(config_path: str, session_name: str) -> Dict[str, Any]:
    wallet_config = load_wallet_config(config_path)
    
    if session_name in wallet_config:
        # Если кошелек уже существует, обновляем accounts_config
        update_accounts_config_wallet(config_path, session_name, wallet_config[session_name]['wallet_address'])
        return wallet_config[session_name]
    
    temp_wallet_path = Path(config_path).parent / f'temp_wallet_{session_name}.json'
    
    try:
        wallet_address = generate_wallet(config_path, str(temp_wallet_path))
        
        with open(temp_wallet_path, 'r') as f:
            wallet_data = json.load(f)
        
        if ':' in wallet_address:
            _, address = wallet_address.split(':')
            wallet_data['raw_address'] = address
        else:
            wallet_data['raw_address'] = wallet_address
            
        wallet_config[session_name] = wallet_data
        save_wallet_config(config_path, wallet_config)
        
        # Обновляем ton_address в accounts_config
        update_accounts_config_wallet(config_path, session_name, wallet_address)
        
        return wallet_config[session_name]
        
    except Exception as e:
        logger.error(f"Error creating wallet: {str(e)}")
        raise
        
    finally:
        if temp_wallet_path.exists():
            os.remove(temp_wallet_path) 