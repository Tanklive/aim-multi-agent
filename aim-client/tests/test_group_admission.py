#!/usr/bin/env python3
"""
group_admission.py 单元测试

覆盖:
  1. GroupInfo / JoinStatus 数据结构
  2. GroupAdmission 初始化 + default_group 配置读取
"""

import json
import sys
from pathlib import Path

# 将 aim-client 和 SDK 加入搜索路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path.home() / ".aim/bin"))  # aim_nats_sdk

from group_admission import GroupInfo, JoinStatus, GroupAdmission


class TestGroupInfo:
    """GroupInfo dataclass 测试"""

    def test_defaults(self):
        g = GroupInfo(group_id="test", name="Test Group", owner="ZS0001", created_at=12345.0)
        assert g.group_id == "test"
        assert g.name == "Test Group"
        assert g.owner == "ZS0001"
        assert g.created_at == 12345.0
        assert g.members == []
        assert g.pending_joins == {}
        assert g.group_type == "chat"
        assert g.is_default is False

    def test_is_default_true(self):
        g = GroupInfo(group_id="grp_trio", name="Default", owner="ZS0001",
                      created_at=10.0, is_default=True)
        assert g.is_default is True

    def test_members_list(self):
        g = GroupInfo(group_id="test", name="T", owner="A", created_at=0.0,
                      members=["A", "B", "C"])
        assert g.members == ["A", "B", "C"]


class TestJoinStatus:
    """JoinStatus 枚举测试"""

    def test_values(self):
        assert JoinStatus.PENDING.value == "pending"
        assert JoinStatus.APPROVED.value == "approved"
        assert JoinStatus.REJECTED.value == "rejected"


class TestGroupAdmissionBasic:
    """GroupAdmission 非 NATS 部分测试"""

    def test_init_defaults(self):
        ga = GroupAdmission()
        # load_global_config() 在模块导入时运行，nats_url 会从 aim.json 读取
        assert isinstance(ga.nats_url, str)
        assert ga.credentials == ""
        assert ga.nc is None
        assert ga.js is None
        assert ga.kv is None
        assert ga._groups == {}
        assert ga._running is False

    def test_init_with_url(self):
        ga = GroupAdmission(nats_url="nats://localhost:4222", credentials="/path/to/creds")
        assert ga.nats_url == "nats://localhost:4222"
        assert ga.credentials == "/path/to/creds"

    def test_subject_constants(self):
        ga = GroupAdmission()
        assert ga.SUBJ_CREATE == "aim.groups.create"
        assert ga.SUBJ_JOIN == "aim.groups.join"
        assert ga.SUBJ_APPROVE == "aim.groups.approve"
        assert ga.SUBJ_MEMBERS == "aim.groups.members"
        assert ga.SUBJ_LIST == "aim.groups.list"


# ── runner ────────────────────────────────────────────────
if __name__ == "__main__":
    import traceback

    passed = 0
    failed = 0

    for cls in [TestGroupInfo, TestJoinStatus, TestGroupAdmissionBasic]:
        instance = cls()
        for name in dir(instance):
            if name.startswith("test_"):
                method = getattr(instance, name)
                try:
                    method()
                    print(f"  ✅ {cls.__name__}.{name}")
                    passed += 1
                except AssertionError as e:
                    print(f"  ❌ {cls.__name__}.{name}: {e}")
                    failed += 1
                except Exception:
                    print(f"  💥 {cls.__name__}.{name}:")
                    traceback.print_exc()
                    failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
