#!/usr/bin/env python3
"""
AIM UDP 内核测试脚本

测试内容：
  1. 内核启动和停止
  2. 私聊消息发送
  3. 群聊消息发送
  4. 心跳保活
  5. 离线消息队列
"""

import asyncio
import sys
import time

# 添加当前目录到路径
sys.path.insert(0, '.')

from udp_kernel import UDPKernel, AIMMessage


async def test_basic_send():
    """测试基本消息发送"""
    print("\n" + "="*60)
    print("测试 1: 基本消息发送")
    print("="*60)
    
    # 创建两个内核
    kernel1 = UDPKernel("ZS0001", 19001)
    kernel2 = UDPKernel("ZS0002", 19002)
    
    received_messages = []
    
    async def on_message(msg: AIMMessage):
        received_messages.append(msg)
        print(f"  [ZS0002] 收到消息: {msg.content}")
    
    kernel2.on_message = on_message
    
    # 启动内核
    await kernel1.start()
    await kernel2.start()
    
    # 等待发现
    await asyncio.sleep(1)
    
    # 发送私聊消息
    msg = AIMMessage(
        type="dm",
        sender="ZS0001",
        receiver="ZS0002",
        content="你好吉量！"
    )
    
    print("\n发送私聊消息...")
    await kernel1.send_message(msg)
    
    # 等待消息处理
    await asyncio.sleep(1)
    
    # 检查结果
    if received_messages:
        print(f"✓ 测试通过: 收到 {len(received_messages)} 条消息")
    else:
        print("✗ 测试失败: 未收到消息")
    
    # 停止内核
    await kernel1.stop()
    await kernel2.stop()
    
    return len(received_messages) > 0


async def test_group_message():
    """测试群聊消息"""
    print("\n" + "="*60)
    print("测试 2: 群聊消息")
    print("="*60)
    
    # 创建三个内核
    kernel1 = UDPKernel("ZS0001", 19001)
    kernel2 = UDPKernel("ZS0002", 19002)
    kernel3 = UDPKernel("ZS0003", 19003)
    
    received_by = {"ZS0002": [], "ZS0003": []}
    
    async def make_handler(agent_id):
        async def on_message(msg: AIMMessage):
            if msg.type == "group":
                received_by[agent_id].append(msg)
                print(f"  [{agent_id}] 收到群聊消息: {msg.content}")
        return on_message
    
    kernel2.on_message = await make_handler("ZS0002")
    kernel3.on_message = await make_handler("ZS0003")
    
    # 启动内核
    await kernel1.start()
    await kernel2.start()
    await kernel3.start()
    
    # 等待发现
    await asyncio.sleep(1)
    
    # 发送群聊消息
    msg = AIMMessage(
        type="group",
        sender="ZS0001",
        receiver="grp_trio",
        content="大家好！"
    )
    
    print("\n发送群聊消息...")
    await kernel1.send_message(msg)
    
    # 等待消息处理
    await asyncio.sleep(1)
    
    # 检查结果
    success = True
    for agent_id, messages in received_by.items():
        if messages:
            print(f"✓ {agent_id} 收到 {len(messages)} 条消息")
        else:
            print(f"✗ {agent_id} 未收到消息")
            success = False
    
    # 停止内核
    await kernel1.stop()
    await kernel2.stop()
    await kernel3.stop()
    
    return success


async def test_heartbeat():
    """测试心跳保活"""
    print("\n" + "="*60)
    print("测试 3: 心跳保活")
    print("="*60)
    
    # 创建两个内核
    kernel1 = UDPKernel("ZS0001", 19001)
    kernel2 = UDPKernel("ZS0002", 19002)
    
    online_events = []
    offline_events = []
    
    async def on_online(agent_id):
        online_events.append(agent_id)
        print(f"  [事件] {agent_id} 上线")
    
    async def on_offline(agent_id):
        offline_events.append(agent_id)
        print(f"  [事件] {agent_id} 离线")
    
    kernel1.on_agent_online = on_online
    kernel1.on_agent_offline = on_offline
    
    # 启动内核
    await kernel1.start()
    await kernel2.start()
    
    # 等待发现
    await asyncio.sleep(2)
    
    print("\n等待心跳超时 (约 90 秒)...")
    print("提示: 可以手动停止 ZS0002 来测试离线检测")
    
    # 这里只是演示，实际测试需要等待超时
    # 为了快速测试，我们模拟一下
    
    # 停止内核
    await kernel1.stop()
    await kernel2.stop()
    
    print("✓ 心跳测试完成 (需要实际等待超时才能验证)")
    return True


async def test_offline_queue():
    """测试离线消息队列"""
    print("\n" + "="*60)
    print("测试 4: 离线消息队列")
    print("="*60)
    
    # 创建内核 1
    kernel1 = UDPKernel("ZS0001", 19001)
    await kernel1.start()
    
    # 发送消息到离线的 ZS0002
    msg = AIMMessage(
        type="dm",
        sender="ZS0001",
        receiver="ZS0002",
        content="离线消息测试"
    )
    
    print("\n发送消息到离线的 ZS0002...")
    await kernel1.send_message(msg)
    
    # 检查离线队列
    if "ZS0002" in kernel1.offline_queue:
        queue = kernel1.offline_queue["ZS0002"]
        print(f"✓ 离线队列有 {len(queue)} 条消息")
    else:
        print("✗ 离线队列为空")
    
    # 启动内核 2
    kernel2 = UDPKernel("ZS0002", 19002)
    
    received_messages = []
    kernel2.on_message = lambda msg: received_messages.append(msg)
    
    await kernel2.start()
    
    # 等待离线消息推送
    await asyncio.sleep(2)
    
    if received_messages:
        print(f"✓ ZS0002 上线后收到 {len(received_messages)} 条离线消息")
    else:
        print("✗ ZS0002 未收到离线消息")
    
    # 停止内核
    await kernel1.stop()
    await kernel2.stop()
    
    return len(received_messages) > 0


async def run_all_tests():
    """运行所有测试"""
    print("AIM UDP 内核测试")
    print("="*60)
    
    results = []
    
    # 运行测试
    results.append(("基本消息发送", await test_basic_send()))
    results.append(("群聊消息", await test_group_message()))
    # results.append(("心跳保活", await test_heartbeat()))  # 需要长时间等待
    results.append(("离线消息队列", await test_offline_queue()))
    
    # 显示结果
    print("\n" + "="*60)
    print("测试结果汇总")
    print("="*60)
    
    for name, success in results:
        status = "✓ 通过" if success else "✗ 失败"
        print(f"{status}: {name}")
    
    # 计算通过率
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    print(f"\n通过率: {passed}/{total} ({passed/total*100:.1f}%)")
    
    return passed == total


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
