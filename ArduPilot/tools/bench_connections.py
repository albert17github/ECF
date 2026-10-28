"""Check a declared static ADC test fixture before changing shared-pad modes."""
import json
from pathlib import Path

def require_unwired_shared_pads(uid=None):
    state=Path(__file__).resolve().parents[1]/'.build/bench-connections.json'
    if not state.exists():
        return
    connection=json.loads(state.read_text(encoding='utf-8'))
    if uid and connection.get('uid')!=uid:
        raise RuntimeError('Bench connection record belongs to another controller')
    if connection.get('adc_fixed_level_jumpers_connected'):
        raise RuntimeError('ADC fixed-level test jumpers are connected. Remove them and update the bench connection record before shared-pad or recovery tests.')
