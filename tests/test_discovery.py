"""Discovery reply parsing, against replies captured from MDP-15000 pumps."""
from jebao.datapoints import PRODUCT_KEY_RETURN_PUMP
from jebao.discovery import JebaoDiscovery

REPLY_04D30Q37 = bytes.fromhex(
    "000000037a0000040016593333544759656f544c7346327241774e674c31674600068856a6fbb99c"
    "0008303444333051333700203661356334376233656133363465636238343162343766353939376131"
    "373735000000000000000275736170692e67697a776974732e636f6d3a383000342e312e3400303330"
    "3330303030"
)
REPLY_04D30Q34 = bytes.fromhex(
    "000000037a0000040016633863377355457641336f61764f50366464506157570006e4b06338b504"
    "0008303444333051333400203661356334376233656133363465636238343162343766353939376131"
    "373735000000000000000275736170692e67697a776974732e636f6d3a383000342e312e3400303330"
    "3330303030"
)


def test_parse_mdp15000_reply():
    device = JebaoDiscovery._parse_discovery_response(REPLY_04D30Q37, "192.168.30.51")
    assert device.device_id == "Y33TGYeoTLsF2rAwNgL1gF"
    assert device.mac_address == "88:56:a6:fb:b9:9c"
    assert device.product_key == "04D30Q37"
    assert device.firmware_version == "04D30Q37"
    assert device.gizwits_product_key == PRODUCT_KEY_RETURN_PUMP
    assert device.model == "MDP-15000"
    assert device.is_mdp20000


def test_parse_shared_key_reply():
    device = JebaoDiscovery._parse_discovery_response(REPLY_04D30Q34, "192.168.30.52")
    assert device.device_id == "c8c7sUEvA3oavOP6ddPaWW"
    assert device.mac_address == "e4:b0:63:38:b5:04"
    assert device.product_key == "04D30Q34"
    assert device.gizwits_product_key == PRODUCT_KEY_RETURN_PUMP
    assert device.is_mdp20000


def test_truncated_reply():
    assert JebaoDiscovery._parse_discovery_response(REPLY_04D30Q34[:30], "1.2.3.4") is None
