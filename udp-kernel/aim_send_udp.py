#!/usr/bin/env python3
"""
AIM UDP 消息发送工具

用法：
  # 发送私聊消息
  python3 aim_send_udp.py --from ZS0003 --to ZS0002 --content "你好吉量"
  
  # 发送群聊消息
  python3 aim_send_udp.py --from ZS0003 --to grp_trio --content "大家好" --group
  
  # 测试心跳
  python3 aim_send_udp.py --from ZS0003 --to "*" --type ping
"""

import argparse
import asyncio
import json
import sys
import time
import uuid

# UDP 端口映射
DEFAULT_PORTS = {
    "ZS0001": 19001,
    "ZS0002": 19002,
    "ZS0003": 19003,
    "ZS0004": 19004,
    "ZS0005": 19005,
}

BROADCAST_PORT = 19000


def create_message(
    msg_type: str,
    sender: str,
    receiver: str,
    content: str,
    meta: dict = None
) -> dict:
    """创建消息"""
    return {
        "v": 1,
        "id": str(uuid.uuid4()),
        "type": msg_type,
        "from": sender,
        "to": receiver,
        "content": content,
        "ts": time.time(),
        "reply_to": None,
        "meta": meta or {}
    }


async def send_message(
    sender: str,
    receiver: str,
    content: str,
    msg_type: str = "dm",
    is_group: bool = False
):
    """发送消息"""
    # 创建消息
    msg = create_message(
        msg_type=msg_type,
        sender=sender,
        receiver=receiver,
        content=content
    )
    
    # 确定目标地址
    if is_group or receiver == "*":
        # 广播消息
        target_addr = ("127.0.0.1", BROADCAST_PORT)
        print(f"广播消息到 {target_addr}")
    else:
        # 私聊消息
        target_port = DEFAULT_PORTS.get(receiver)
        if not target_port:
            print(f"错误: 未知的接收者 {receiver}")
            return False
        target_addr = ("127.0.0.1", target_port)
        print(f"发送消息到 {target_addr}")
    
    # 发送 UDP 数据报
    try:
        # 创建 UDP 套接字
        loop = asyncio.get_event_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: asyncio.DatagramProtocol(),
            remote_addr=target_addr
        )
        
        # 发送消息
        data = json.dumps(msg, ensure_ascii=False).encode('utf-8')
        transport.sendto(data)
        
        print(f"消息已发送: {msg['id']}")
        print(f"  类型: {msg_type}")
        print(f"  发送者: {sender}")
        print(f"  接收者: {receiver}")
        print(f"  内容: {content[:50]}{'...' if len(content) > 50 else ''}")
        
        # 关闭传输
        transport.close()
        
        return True
    except Exception as e:
        print(f"发送失败: {e}")
        return False


async def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="AIM UDP 消息发送工具")
    parser.add_argument("--from", dest="sender", required=True, help="发送者 Agent ID")
    parser.add_argument("--to", dest="receiver", required=True, help="接收者 Agent ID 或群组名")
    parser.add_argument("--content", default="", help="消息内容")
    parser.add_argument("--type", dest="msg_type", default="dm", 
                       choices=["dm", "group", "ping", "pong", "join", "leave", "ack"],
                       help="消息类型")
    parser.add_argument("--group", action="store_true", help="发送群聊消息")
    parser.add_argument("--file", help="从文件读取消息内容")
    args = parser.parse_args()
    
    # 读取消息内容
    content = args.content
    if args.file:
        try:
            with open(args.file, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            print(f"读取文件失败: {e}")
            return
    
    if not content:
        print("错误: 消息内容为空")
        return
    
    # 确定消息类型
    msg_type = args.msg_type
    if args.group:
        msg_type = "group"
    
    # 发送消息
    success = await send_message(
        sender=args.sender,
        receiver=args.receiver,
        content=content,
        msg_type=msg_type,
        is_group=args.group or args.receiver == "*"
    )
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
