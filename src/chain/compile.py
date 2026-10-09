import solcx
import json
import os

def compile_contract():
    compiled_path = "src/chain/contracts/RegistryCompiled.json"
    
    # If the contract is already compiled and present, use it directly
    if os.path.exists(compiled_path):
        print(f"[Contract] Using pre-compiled contract artifact: {compiled_path}")
        return

    print("[Contract] Installing / Setting solc version 0.8.20...")
    try:
        solcx.set_solc_version('0.8.20')
    except Exception:
        try:
            solcx.install_solc('0.8.20')
            solcx.set_solc_version('0.8.20')
        except Exception as e:
            print(f"[Contract] Warning: solc installation error: {e}")
            if os.path.exists(compiled_path):
                print("[Contract] Fallback to existing compiled artifact.")
                return
            raise
    
    print("[Contract] Compiling Registry.sol...")
    contract_file = "src/chain/contracts/Registry.sol"
    with open(contract_file, "r", encoding="utf-8") as f:
        contract_source = f.read()

    compiled_sol = solcx.compile_source(
        contract_source,
        output_values=['abi', 'bin']
    )
    
    contract_id, contract_interface = compiled_sol.popitem()
    
    # Save the ABI and Bytecode
    with open(compiled_path, "w", encoding="utf-8") as f:
        json.dump(contract_interface, f, indent=4)
        
    print(f"[Contract] Compiled successfully! Saved to {compiled_path}")

if __name__ == "__main__":
    compile_contract()
