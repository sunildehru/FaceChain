from .provider import get_w3
from typing import Dict, Any

def anchor_record(record_hash_hex: str, contract_address: str, abi: list) -> Dict[str, Any]:
    """
    Anchors a canonical SHA-256 fingerprint hash into the Solidity Registry contract.
    Returns transaction execution receipt and block telemetry.
    """
    w3 = get_w3()
    account = w3.eth.accounts[0]
    
    contract = w3.eth.contract(address=contract_address, abi=abi)
    
    if not record_hash_hex.startswith("0x"):
        record_hash_hex = "0x" + record_hash_hex
        
    bytes32_hash = w3.to_bytes(hexstr=record_hash_hex)
    metadata_uri = f"ipfs://facechain-{record_hash_hex[2:10]}"
    
    tx_hash = contract.functions.anchorRecord(bytes32_hash, metadata_uri).transact({'from': account})
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    
    raw_hex = receipt.transactionHash.hex()
    tx_hash_formatted = raw_hex if raw_hex.startswith("0x") else f"0x{raw_hex}"
    
    return {
        "tx_hash": tx_hash_formatted,
        "block_number": receipt.blockNumber,
        "gas_used": receipt.gasUsed,
        "submitter": account,
        "contract_address": contract_address,
        "metadata_uri": metadata_uri,
        "status": bool(receipt.status)
    }
