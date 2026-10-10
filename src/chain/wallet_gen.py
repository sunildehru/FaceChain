from eth_account import Account
import secrets
import os

def generate_wallet():
    priv = secrets.token_hex(32)
    private_key = "0x" + priv
    acct = Account.from_key(private_key)
    
    # Save to .env
    env_content = f"PRIVATE_KEY={private_key}\nPOLYGON_AMOY_RPC_URL=https://rpc-amoy.polygon.technology\n"
    with open(".env", "w") as f:
        f.write(env_content)
        
    print(f"Address: {acct.address}")
    
if __name__ == "__main__":
    generate_wallet()
