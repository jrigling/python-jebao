"""Basic control example for Jebao MDP-20000."""
import asyncio
import logging

from jebao import MDP20000Device, discover_devices

# Enable logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


async def discovery_example():
    """Discover devices on network."""
    print("\n=== Discovery Example ===")

    devices = await discover_devices(timeout=3.0)

    print(f"\nFound {len(devices)} device(s):")
    for device in devices:
        print(f"  - {device.model} ({device.device_id}) at {device.ip_address}")

    return devices


async def basic_control_example(host: str):
    """Basic control example."""
    print(f"\n=== Basic Control Example ({host}) ===")

    async with MDP20000Device(host=host) as pump:
        # Ensure manual mode
        print("\nEnsuring manual mode...")
        await pump.ensure_manual_mode()

        # Get status
        print("\nGetting initial status...")
        await pump.update()
        print(f"State: {pump.state.name}")
        print(f"Speed: {pump.speed}%")
        print(f"Is on: {pump.is_on}")

        # Turn on and set speed
        print("\nTurning on...")
        await pump.turn_on()
        await asyncio.sleep(1)

        print("Setting speed to 50%...")
        await pump.set_speed(50)
        await asyncio.sleep(2)

        print("Setting speed to 75%...")
        await pump.set_speed(75)
        await asyncio.sleep(2)

        # Check status
        await pump.update()
        print(f"\nCurrent state: {pump.state.name} @ {pump.speed}%")

        # Turn off
        print("\nTurning off...")
        await pump.turn_off()
        await asyncio.sleep(1)

        # Final status
        await pump.update()
        print(f"Final state: {pump.state.name}")


async def feed_mode_example(host: str):
    """Feed mode example."""
    print(f"\n=== Feed Mode Example ({host}) ===")

    async with MDP20000Device(host=host) as pump:
        await pump.ensure_manual_mode()

        # Turn on at 75%
        print("\nStarting pump at 75%...")
        await pump.turn_on()
        await pump.set_speed(75)
        await asyncio.sleep(2)

        # Start 1-minute feed
        print("\nStarting 1-minute feed mode...")
        await pump.start_feed(minutes=1)

        # Check status
        await pump.update()
        print(f"State: {pump.state.name} (should be FEED)")

        # Wait a bit then check again
        print("\nWaiting 5 seconds...")
        await asyncio.sleep(5)

        # Cancel feed
        print("Canceling feed, resuming at 50%...")
        await pump.cancel_feed(resume_speed=50)

        # Check status
        await pump.update()
        print(f"State: {pump.state.name} @ {pump.speed}% (should be ON @ 50%)")

        # Turn off
        await asyncio.sleep(2)
        await pump.turn_off()


async def monitoring_example(host: str, duration: int = 60):
    """Monitor pump status for specified duration."""
    print(f"\n=== Monitoring Example ({host}, {duration}s) ===")

    pump = MDP20000Device(host=host)

    try:
        await pump.connect()
        await pump.ensure_manual_mode()

        # Turn on at 60%
        await pump.turn_on()
        await pump.set_speed(60)

        print(f"\nMonitoring pump for {duration} seconds...")
        print("Press Ctrl+C to stop\n")

        end_time = asyncio.get_event_loop().time() + duration
        while asyncio.get_event_loop().time() < end_time:
            await pump.update()
            print(
                f"[{asyncio.get_event_loop().time():.0f}] "
                f"State: {pump.state.name:8s} | "
                f"Speed: {pump.speed:3d}% | "
                f"Connected: {pump.is_connected}"
            )
            await asyncio.sleep(5)

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        await pump.turn_off()
        await pump.disconnect()


async def main():
    """Run examples."""
    # Discover devices
    devices = await discovery_example()

    if not devices:
        print("\nNo devices found. Make sure pumps are on the network.")
        return

    # Use first MDP-20000 found
    mdp20000_devices = [d for d in devices if d.is_mdp20000]
    if not mdp20000_devices:
        print("\nNo MDP-20000 devices found.")
        return

    device = mdp20000_devices[0]
    host = device.ip_address

    print(f"\nUsing device: {device.device_id} at {host}")

    # Run examples (uncomment the ones you want to try)
    await basic_control_example(host)
    # await feed_mode_example(host)
    # await monitoring_example(host, duration=30)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting...")
