def crc16_xmodem(data: bytes) -> int:
    """CRC16/XMODEM, init 0x0000, poly 0x1021.

    Test vector: b"123456789" -> 0x31C3.
    """

    crc = 0x0000
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc

