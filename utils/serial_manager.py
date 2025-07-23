import asyncio
import serial
import serial.tools.list_ports
from typing import Dict, List, Tuple

from .my_log import logger


class SerialManager:
    def __init__(self):
        self.connections: Dict[str, Tuple[serial.Serial, asyncio.Task]] = {}
        self.buffers: Dict[str, bytearray] = {}

    async def list_ports(self) -> List[str]:
        # 列出所有可用的串口
        ports = serial.tools.list_ports.comports()
        return [port.device for port in ports]

    async def connect(self, port: str, baudrate: int = 115200, timeout: int = 1) -> dict:
        # 连接到指定串口
        if port in self.connections:
            logger.warning(f"{port} 已经连接")
            return {'ret': False, 'msg': f'{port} 已经连接'}

        try:
            # loop = asyncio.get_running_loop()
            serial_conn = serial.Serial(port, baudrate, timeout=timeout)
            task = None
            # task = loop.run_in_executor(None, self._read_serial, port, serial_conn)
            self.connections[port] = (serial_conn, task)
            self.buffers[port] = bytearray()
            logger.info(f"已连接到 {port}")
            return {'ret': True, 'msg': f'已连接到 {port}'}
        except Exception as e:
            logger.error(f"连接到 {port} 时出错: {e}")
            return {'ret': False, 'msg': f'连接到 {port} 时出错: {e}'}

    async def disconnect(self, port: str) -> dict:
        # 断开指定串口连接
        if port not in self.connections:
            logger.warning(f"{port} 未连接，无需关闭")
            return {'ret': False, 'msg': f'{port} 未连接'}

        serial_conn, task = self.connections.pop(port)
        serial_conn.close()
        # task.cancel()
        del self.buffers[port]
        logger.info(f"已断开与 {port} 的连接")
        return {'ret': True, 'msg': f'已断开与 {port} 的连接'}

    async def send_data(self, port: str, data: str, data_type: str = 'ascii', timeout: float = 1.0) -> str:
        # 发送数据并等待返回，带超时机制
        if port not in self.connections:
            logger.warning(f"{port} 未连接")
            return {'ret': False, 'msg': f"{port} 未连接"}

        serial_conn, _ = self.connections[port]
        try:
            self.buffers[port] = bytearray()  # 清空缓冲区

            # 根据 data_type 进行编码
            if data_type in ['ascii', 'ASCII']:
                encoded_data = data.encode()
            elif data_type in ['hex', 'HEX']:
                encoded_data = bytes.fromhex(data)
            else:
                logger.error(f"无效的数据类型: {data_type}")
                return {'ret': False, 'msg': f"无效的数据类型: {data_type}"}

            serial_conn.write(encoded_data)
            logger.info(f"发送{data_type}数据：{data}")
            return {'ret': True, "msg": f"发送{data_type}数据：{data}"}
            # resp_json = await self._read_response(port, timeout)
            # return resp_json
        except Exception as e:
            logger.error(f"发送数据到 {port} 时出错: {e}")
            return {'ret': False, 'msg': f"发送数据到 {port} 时出错: {e}"}

    async def _read_response(self, port: str, timeout: float) -> str:
        # 读取串口返回的数据，带超时机制
        try:
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(None, self._wait_for_data, port)
            response = await asyncio.wait_for(future, timeout)
            return {'ret': True, "msg": f"收到返回HEX数据：{response}"}
        except asyncio.TimeoutError:
            logger.error(f"读取 {port} 的数据超时")
            return {'ret': True, "msg": f"读取 {port} 的数据超时"}
        except Exception as e:
            logger.error(f"读取 {port} 数据时出错: {e}")
            return {'ret': False, "msg": f"读取 {port} 数据时出错: {e}"}

    def _wait_for_data(self, port: str) -> str:
        # 等待数据到达，并从缓冲区读取
        while True:
            if port not in self.buffers:
                return ""
            buffer = self.buffers[port]
            if buffer:
                response = buffer[:]
                self.buffers[port] = bytearray()  # 清空缓冲区
                return self._process_data("hex", response)

    def _read_serial(self, port: str, serial_conn: serial.Serial):
        try:
            # 后台任务：持续读取串口数据并进行处理
            while True:
                data = serial_conn.read(1024)  # 读取一定量的数据
                if data:
                    self.buffers[port].extend(data)
                    logger.info(f"从 {port} 接收到数据: {self._process_data('hex', data)}")
        except serial.SerialException as e:
            logger.error(f"{port} 串口异常: {e}")
        except Exception as e:
            logger.error(f"读取 {port} 时出错: {e}")
        finally:
            if port in self.connections:
                serial_conn.close()
                del self.connections[port]
                del self.buffers[port]
                logger.info(f"{port} 已断开连接")


    def _process_data(self, type: str, data: bytes) -> str:
        try:
            if type == "hex":
                # 返回十六进制表示
                return data.hex()
            elif type == "str":
                # 尝试解码为字符串
                return data.decode('utf-8')
            else:
                # 未知类型，返回十六进制表示
                return data.hex()
        except UnicodeDecodeError:
            # 不能解码为字符串时，返回十六进制表示
            return data.hex()

async def main():
    serial_manager = SerialManager()

    # 列出所有可用的串口
    ports = await serial_manager.list_ports()
    logger.info(f"可用的串口: {ports}")

    # 连接到一个串口
    if ports:
        port = ports[0]
        connected = await serial_manager.connect(port)
        if connected['ret']:
            # 发送数据并等待返回
            response = await serial_manager.send_data(port, "Hello", timeout=2)
            logger.info(f"返回: {response}")

            # 断开与串口的连接
            await serial_manager.disconnect(port)

if __name__ == "__main__":
    logger.add("serial_manager.log", rotation="1 MB")
    asyncio.run(main())