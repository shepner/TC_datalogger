#!/usr/bin/env python3
"""Quick test to check API response format."""

import json
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.api_client import TornCityAPIClient
from src.config import Config

def main():
    config = Config("config/TC_API_config.json")
    api_key = config.get_api_key("faction_40832")
    
    if not api_key:
        print("ERROR: API key not found")
        return
    
    api_client = TornCityAPIClient(
        api_key=api_key,
        base_url="https://api.torn.com"
    )
    
    print("Testing /faction/chains endpoint...")
    try:
        # Try with selection parameter
        print("\n1. Testing /faction/chains with selection=chains...")
        response1 = api_client._make_request(
            "https://api.torn.com/faction/chains",
            {"selection": "chains"}
        )
        print(f"Response type: {type(response1)}")
        if isinstance(response1, dict):
            print(f"Keys: {list(response1.keys())}")
            if "chains" in response1:
                print(f"Found 'chains' key: {response1['chains']}")
        
        # Try v2 endpoint
        print("\n2. Testing /v2/faction/chains...")
        response2 = api_client._make_request(
            "https://api.torn.com/v2/faction/chains",
            {}
        )
        print(f"Response type: {type(response2)}")
        if isinstance(response2, dict):
            print(f"Keys: {list(response2.keys())}")
        
        # Use the one that works
        response = response2 if isinstance(response2, dict) and "chains" in response2 else response1
        
        print(f"\nResponse type: {type(response)}")
        print(f"\nResponse keys: {list(response.keys()) if isinstance(response, dict) else 'Not a dict'}")
        
        # Check all keys for chain-related data
        if isinstance(response, dict):
            for key in response.keys():
                if 'chain' in key.lower():
                    print(f"\nFound chain-related key '{key}': {type(response[key])}")
                    if isinstance(response[key], (list, dict)):
                        print(f"  Value: {json.dumps(response[key], indent=2)[:500]}")
        
        # The API might return chains in a different structure
        # Let's check if there's a nested structure
        print(f"\nFull response (first 3000 chars):")
        print(json.dumps(response, indent=2)[:3000])
        
        # Try to extract chain IDs from various possible locations
        chain_ids = []
        if isinstance(response, dict):
            # Check for chains key
            if "chains" in response:
                chains_data = response["chains"]
                if isinstance(chains_data, list):
                    chain_ids = chains_data
                elif isinstance(chains_data, dict):
                    # Maybe it's a dict with chain IDs as keys
                    chain_ids = list(chains_data.keys())
        
        print(f"\nExtracted chain IDs: {chain_ids[:5]}... (showing first 5)")
        print(f"Number of chains: {len(chain_ids)}")
        
        # Test chain report endpoint with first chain ID
        if chain_ids:
            test_chain_id = chain_ids[0]
            print(f"\n3. Testing chain report for chain ID {test_chain_id}...")
            
            # Try different endpoint formats
            endpoints_to_try = [
                f"/faction/{test_chain_id}/chainreport",
                f"/v2/faction/{test_chain_id}/chainreport",
                f"/faction/chainreport",
            ]
            
            for endpoint in endpoints_to_try:
                try:
                    params = {} if "chainreport" in endpoint else {"chainId": test_chain_id}
                    if endpoint == "/faction/chainreport":
                        params = {"chainId": test_chain_id}
                    
                    print(f"  Trying: {endpoint} with params {params}")
                    response3 = api_client._make_request(
                        f"https://api.torn.com{endpoint}",
                        params
                    )
                    print(f"  SUCCESS! Response type: {type(response3)}")
                    if isinstance(response3, dict):
                        print(f"  Response keys: {list(response3.keys())[:10]}")
                    break
                except Exception as e:
                    print(f"  Failed: {e}")
                    continue
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

