from hyperliquid.info import Info
from hyperliquid.utils import constants

info = Info(constants.MAINNET_API_URL, skip_ws=True)
meta = info.meta()
print("Available assets:", [asset['name'] for asset in meta['universe']]) 