python3 << 'EOF'
import asyncio
import websockets
import json
import aiohttp

async def get_snapshot():
    # First, get the list of trials
    api_url = "http://127.0.0.1:8000/api/trials"
    async with aiohttp.ClientSession() as session:
        async with session.get(api_url) as response:
            trials = await response.json()
            
    if not trials:
        print('✗ No trials found')
        return
    
    # Get the first trial's ID
    trial_id = trials[0]['id']
    print(f'Using trial ID: {trial_id}')
    
    # Connect to the websocket
    uri = f"ws://localhost:8000/ws/trials/{trial_id}/stream"
    async with websockets.connect(uri) as websocket:
        async for message in websocket:
            msg = json.loads(message)
            if msg.get('type') == 'snapshot':
                with open('snapshot_data.json', 'w') as f:
                    json.dump(msg['data'], f, indent=2)
                print('✓ Snapshot data saved to snapshot_data.json')
                break

asyncio.run(get_snapshot())