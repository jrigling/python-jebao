"""UDP discovery for Jebao devices with multi-subnet support."""
import asyncio
import logging
import socket
from dataclasses import dataclass
from typing import List, Optional, Set

import netifaces

from .const import (
    DISCOVERY_TIMEOUT,
    MODEL_MD44,
    MODEL_MDP20000,
    PRODUCT_KEY_MD44,
    PRODUCT_KEY_MDP20000,
    UDP_DISCOVERY_PORT,
    UDP_LISTEN_PORT,
)
from .exceptions import JebaoError

_LOGGER = logging.getLogger(__name__)


@dataclass
class DiscoveredDevice:
    """Represents a discovered Jebao device."""

    device_id: str
    mac_address: str
    product_key: str
    ip_address: str
    model: str
    firmware_version: Optional[str] = None

    @property
    def is_mdp20000(self) -> bool:
        """Check if device is MDP-20000."""
        return self.model == MODEL_MDP20000

    @property
    def is_md44(self) -> bool:
        """Check if device is MD-4.4."""
        return self.model == MODEL_MD44


class JebaoDiscovery:
    """UDP discovery for Jebao devices with multi-subnet support."""

    # Discovery request payload
    DISCOVERY_REQUEST = bytes.fromhex("00 00 00 03 03 00 00 03")

    def __init__(self):
        """Initialize discovery."""
        self._discovered: Set[str] = set()  # Track device IDs

    async def discover(
        self,
        timeout: float = DISCOVERY_TIMEOUT,
        interfaces: Optional[List[str]] = None,
    ) -> List[DiscoveredDevice]:
        """Discover Jebao devices on network.

        This method broadcasts discovery requests on all available network
        interfaces (or specified interfaces) and collects responses.

        Args:
            timeout: Discovery timeout in seconds
            interfaces: Optional list of network interface names to use.
                       If None, all interfaces will be used.

        Returns:
            List of discovered devices

        Example:
            >>> discovery = JebaoDiscovery()
            >>> devices = await discovery.discover()
            >>> for device in devices:
            ...     print(f"Found {device.model} at {device.ip_address}")
        """
        self._discovered.clear()

        # Get network interfaces to scan
        if interfaces is None:
            interfaces = self._get_all_interfaces()
        else:
            # Validate specified interfaces exist
            available = self._get_all_interfaces()
            interfaces = [iface for iface in interfaces if iface in available]

        if not interfaces:
            _LOGGER.warning("No network interfaces found for discovery")
            return []

        _LOGGER.info(
            "Starting discovery on %d interface(s): %s",
            len(interfaces),
            ", ".join(interfaces),
        )

        # Create discovery tasks for each interface
        tasks = []
        for interface in interfaces:
            task = asyncio.create_task(
                self._discover_on_interface(interface, timeout)
            )
            tasks.append(task)

        # Wait for all discovery tasks to complete
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Combine results from all interfaces
        devices: List[DiscoveredDevice] = []
        for result in results:
            if isinstance(result, Exception):
                _LOGGER.error("Discovery task failed: %s", result)
            elif isinstance(result, list):
                devices.extend(result)

        # Deduplicate by device ID
        unique_devices = {device.device_id: device for device in devices}
        devices = list(unique_devices.values())

        _LOGGER.info("Discovery complete: found %d device(s)", len(devices))
        return devices

    async def _discover_on_interface(
        self, interface: str, timeout: float
    ) -> List[DiscoveredDevice]:
        """Discover devices on a specific network interface.

        Args:
            interface: Network interface name (e.g., 'eth0', 'eth1')
            timeout: Discovery timeout

        Returns:
            List of discovered devices on this interface
        """
        devices: List[DiscoveredDevice] = []

        # Get broadcast address for interface
        broadcast_addr = self._get_broadcast_address(interface)
        if not broadcast_addr:
            _LOGGER.warning(
                "Could not determine broadcast address for %s", interface
            )
            return devices

        _LOGGER.debug(
            "Discovering on %s (broadcast: %s)", interface, broadcast_addr
        )

        # Create UDP socket bound to this interface
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            # Bind to specific interface's IP to ensure broadcast goes out correct interface
            # Use port 37479 to match what the official app uses
            local_ip = self._get_interface_ip(interface)
            if local_ip:
                sock.bind((local_ip, 37479))
            else:
                sock.bind(("", 37479))

            sock.setblocking(False)

            # Send broadcast
            _LOGGER.debug(
                "Sending discovery request to %s:%d from %s",
                broadcast_addr,
                UDP_DISCOVERY_PORT,
                local_ip or "any",
            )
            sock.sendto(
                self.DISCOVERY_REQUEST, (broadcast_addr, UDP_DISCOVERY_PORT)
            )
            _LOGGER.debug("Discovery request sent: %s", self.DISCOVERY_REQUEST.hex())

            # Listen for responses
            end_time = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < end_time:
                remaining = end_time - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break

                try:
                    # Use asyncio to avoid blocking
                    loop = asyncio.get_event_loop()
                    data, addr = await asyncio.wait_for(
                        loop.sock_recvfrom(sock, 2048), timeout=remaining
                    )

                    _LOGGER.debug(
                        "Received %d bytes from %s on %s",
                        len(data),
                        addr[0],
                        interface,
                    )

                    device = self._parse_discovery_response(data, addr[0])
                    if device and device.device_id not in self._discovered:
                        self._discovered.add(device.device_id)
                        devices.append(device)
                        _LOGGER.info(
                            "Discovered %s (%s) at %s on %s",
                            device.model,
                            device.device_id,
                            device.ip_address,
                            interface,
                        )

                except asyncio.TimeoutError:
                    # Timeout waiting for response, discovery complete
                    _LOGGER.debug("No more responses on %s (timeout)", interface)
                    break
                except Exception as err:
                    _LOGGER.debug("Error receiving discovery response: %s", err)
                    break

            _LOGGER.debug("Discovery loop complete on %s, found %d devices", interface, len(devices))

        except Exception as err:
            _LOGGER.error("Discovery failed on %s: %s", interface, err)
        finally:
            if sock:
                sock.close()

        return devices

    @staticmethod
    def _get_all_interfaces() -> List[str]:
        """Get all available network interfaces.

        Returns:
            List of interface names
        """
        interfaces = []
        try:
            for iface in netifaces.interfaces():
                # Skip loopback
                if iface.startswith("lo"):
                    continue

                # Check if interface has IPv4 address
                addrs = netifaces.ifaddresses(iface)
                if netifaces.AF_INET in addrs:
                    interfaces.append(iface)

        except Exception as err:
            _LOGGER.error("Error enumerating interfaces: %s", err)

        return interfaces

    @staticmethod
    def _get_interface_ip(interface: str) -> Optional[str]:
        """Get IPv4 address of interface.

        Args:
            interface: Interface name

        Returns:
            IP address or None
        """
        try:
            addrs = netifaces.ifaddresses(interface)
            if netifaces.AF_INET in addrs:
                return addrs[netifaces.AF_INET][0]["addr"]
        except Exception as err:
            _LOGGER.debug("Error getting IP for %s: %s", interface, err)

        return None

    @staticmethod
    def _get_broadcast_address(interface: str) -> Optional[str]:
        """Get broadcast address for interface.

        Args:
            interface: Interface name

        Returns:
            Broadcast address or None
        """
        try:
            addrs = netifaces.ifaddresses(interface)
            if netifaces.AF_INET in addrs:
                # Some systems provide broadcast directly
                if "broadcast" in addrs[netifaces.AF_INET][0]:
                    return addrs[netifaces.AF_INET][0]["broadcast"]

                # Calculate broadcast from IP and netmask
                ip = addrs[netifaces.AF_INET][0]["addr"]
                netmask = addrs[netifaces.AF_INET][0].get("netmask")

                if ip and netmask:
                    # Convert to integers
                    ip_int = int.from_bytes(
                        socket.inet_aton(ip), byteorder="big"
                    )
                    mask_int = int.from_bytes(
                        socket.inet_aton(netmask), byteorder="big"
                    )

                    # Calculate broadcast
                    broadcast_int = ip_int | (~mask_int & 0xFFFFFFFF)
                    broadcast = socket.inet_ntoa(
                        broadcast_int.to_bytes(4, byteorder="big")
                    )
                    return broadcast

        except Exception as err:
            _LOGGER.debug(
                "Error getting broadcast address for %s: %s", interface, err
            )

        return None

    @staticmethod
    def _parse_discovery_response(
        data: bytes, ip_address: str
    ) -> Optional[DiscoveredDevice]:
        """Parse discovery response packet.

        Args:
            data: Raw response data (127 bytes)
            ip_address: Source IP address

        Returns:
            DiscoveredDevice or None if parsing failed
        """
        try:
            if len(data) < 46:
                _LOGGER.debug("Response too short: %d bytes", len(data))
                return None

            # Verify header
            if data[0:4] != b"\x00\x00\x00\x03":
                _LOGGER.debug("Invalid header")
                return None

            # Extract fields with length-prefixed parsing
            # Device ID: 2-byte length at 8-9, then string data
            device_id_length = int.from_bytes(data[8:10], "big")
            device_id_start = 10
            device_id_end = device_id_start + device_id_length
            device_id = data[device_id_start:device_id_end].decode("ascii")

            # MAC address follows device ID (6 bytes)
            mac_start = device_id_end + 2  # Skip 2-byte separator
            mac_bytes = data[mac_start : mac_start + 6]
            mac_address = ":".join(f"{b:02x}" for b in mac_bytes)

            # Product key is 8 bytes as ASCII, after device_id + MAC + separators
            # After device_id (var len) + separator (2) + MAC (6) + separator (2)
            product_key_start = device_id_end + 2 + 6 + 2
            product_key = data[product_key_start : product_key_start + 8].rstrip(b"\x00").decode("ascii")

            # Determine model from product key
            if product_key == PRODUCT_KEY_MDP20000:
                model = MODEL_MDP20000
            elif product_key == PRODUCT_KEY_MD44:
                model = MODEL_MD44
            else:
                _LOGGER.warning("Unknown product key: %s", product_key)
                model = f"Unknown ({product_key})"

            return DiscoveredDevice(
                device_id=device_id,
                mac_address=mac_address,
                product_key=product_key,
                ip_address=ip_address,
                model=model,
            )

        except Exception as err:
            _LOGGER.error("Error parsing discovery response: %s", err)
            return None


async def discover_devices(
    timeout: float = DISCOVERY_TIMEOUT,
    interfaces: Optional[List[str]] = None,
) -> List[DiscoveredDevice]:
    """Convenience function to discover devices.

    Args:
        timeout: Discovery timeout in seconds
        interfaces: Optional list of interface names to scan

    Returns:
        List of discovered devices

    Example:
        >>> devices = await discover_devices()
        >>> mdp20000_devices = [d for d in devices if d.is_mdp20000]
    """
    discovery = JebaoDiscovery()
    return await discovery.discover(timeout=timeout, interfaces=interfaces)
