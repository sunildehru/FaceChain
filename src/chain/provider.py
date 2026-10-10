import json
import os
from web3 import Web3
from eth_tester import EthereumTester
from typing import Dict, Any, Tuple, List

# Initialize the local in-memory blockchain
tester = EthereumTester()
provider = Web3.EthereumTesterProvider(tester)
w3 = Web3(provider)

def get_w3() -> Web3:
    """Returns the connected Web3 instance."""
    return w3

def get_chain_telemetry(contract_address: str = None) -> Dict[str, Any]:
    """Returns real-time telemetry from the in-memory EVM."""
    return {
        "network": "EthereumTester EVM (In-Memory)",
        "chain_id": w3.eth.chain_id,
        "block_number": w3.eth.block_number,
        "accounts_count": len(w3.eth.accounts),
        "primary_account": w3.eth.accounts[0] if w3.eth.accounts else None,
        "contract_address": contract_address,
        "status": "OPERATIONAL"
    }

def deploy_registry_contract() -> Tuple[str, List]:
    """
    Compiles and deploys the Registry contract to the local testnet.
    Returns the contract address and its ABI.
    """
    compiled_path = "src/chain/contracts/RegistryCompiled.json"
    if not os.path.exists(compiled_path):
        alt_path = os.path.join(os.path.dirname(__file__), "contracts", "RegistryCompiled.json")
        if os.path.exists(alt_path):
            compiled_path = alt_path
        else:
            raise FileNotFoundError(f"{compiled_path} not found. Did you compile the contract?")
        
    with open(compiled_path, "r", encoding="utf-8") as f:
        contract_interface = json.load(f)
        
    contract_abi = contract_interface['abi']
    bytecode = contract_interface['bin']
    
    # Deploy from the first test account
    account = w3.eth.accounts[0]
    Registry = w3.eth.contract(abi=contract_abi, bytecode=bytecode)
    
    print("[Blockchain] Deploying Registry contract...")
    tx_hash = Registry.constructor().transact({'from': account})
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    
    contract_address = tx_receipt.contractAddress
    print(f"[Blockchain] Contract successfully deployed at: {contract_address}")
    
    return contract_address, contract_abi
