from src.chain.provider import get_w3
from src.fingerprint import hash_record
from typing import Dict, Any

def verify_on_chain(record: dict, contract_address: str, abi: list) -> Dict[str, Any]:
    """
    Verifies a canonical record against the on-chain registry state.
    Returns verification boolean and on-chain record attributes.
    """
    w3 = get_w3()
    contract = w3.eth.contract(address=contract_address, abi=abi)
    
    computed_hash_hex = hash_record(record)
    
    if not computed_hash_hex.startswith("0x"):
        computed_hash_hex = "0x" + computed_hash_hex
        
    bytes32_hash = w3.to_bytes(hexstr=computed_hash_hex)
    
    try:
        result = contract.functions.getRecord(bytes32_hash).call()
        submitter, anchored_at, metadata_uri = result
        if anchored_at > 0:
            return {
                "is_verified": True,
                "submitter": submitter,
                "anchored_at": anchored_at,
                "metadata_uri": metadata_uri,
                "hash": computed_hash_hex,
                "message": "Cryptographic proof confirmed on-chain."
            }
        return {
            "is_verified": False,
            "hash": computed_hash_hex,
            "message": "Record found with zero timestamp."
        }
    except Exception:
        return {
            "is_verified": False,
            "hash": computed_hash_hex,
            "message": "Record hash not found on-chain (reverted)."
        }
