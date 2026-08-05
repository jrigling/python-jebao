from jebao.const import MODEL_MD44
from jebao.discovery import JebaoDiscovery


def field(value: bytes) -> bytes:
    return bytes([0, len(value)]) + value


def test_discovery_recognizes_md44_long_product_key():
    response = b"".join(
        (
            bytes.fromhex("0000000300000004"),
            field(b"sanitized-device-id"),
            field(bytes.fromhex("001122334455")),
            field(b"04X3000R"),
            field(b"1aa33c38ba9d4b78a9e7796705b2fad7"),
        )
    )
    device = JebaoDiscovery._parse_discovery_response(response, "192.0.2.10")
    assert device is not None
    assert device.model == MODEL_MD44
    assert device.mac_address == "00:11:22:33:44:55"
    assert device.product_key == "1aa33c38ba9d4b78a9e7796705b2fad7"
