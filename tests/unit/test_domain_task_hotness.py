"""
领域模型中热度模式相关的验证测试。
覆盖：TaskCreate/TaskGenerateRequest 对 hotness 配置的校验逻辑。
"""
import pytest

from src.domain.models.task import HotnessConfig, Task, TaskCreate, TaskGenerateRequest


class TestHotnessConfig:
    def test_basic_config_creation(self):
        config = HotnessConfig(
            collect_per_minute=0.05,
            want_per_minute=0.03,
            browse_per_minute=1.0,
            condition_logic="or",
        )
        assert config.collect_per_minute == 0.05
        assert config.want_per_minute == 0.03
        assert config.browse_per_minute == 1.0
        assert config.condition_logic == "or"
        assert config.max_monitor_hours is None
        assert config.max_check_count is None
        assert config.max_recheck_per_run == 10

    def test_all_optional_fields(self):
        config = HotnessConfig(
            collect_per_minute=0.01,
            max_monitor_hours=24.0,
            max_check_count=5,
            max_recheck_per_run=20,
            condition_logic="and",
        )
        assert config.max_monitor_hours == 24.0
        assert config.max_check_count == 5
        assert config.max_recheck_per_run == 20
        assert config.condition_logic == "and"

    def test_empty_config_defaults(self):
        config = HotnessConfig()
        assert config.collect_per_minute is None
        assert config.want_per_minute is None
        assert config.browse_per_minute is None
        assert config.condition_logic == "or"
        assert config.max_recheck_per_run == 10

    def test_extra_fields_ignored(self):
        """extra='ignore' 配置下多余字段应被忽略"""
        config = HotnessConfig(
            collect_per_minute=0.01,
            unknown_field="should be ignored",
        )
        assert config.collect_per_minute == 0.01
        assert not hasattr(config, "unknown_field")


class TestTaskCreateHotnessValidation:
    def test_hotness_mode_requires_config(self):
        """hotness 模式下必须提供 hotness_config"""
        with pytest.raises(ValueError, match="热度判断模式.*hotness_config"):
            TaskCreate(
                task_name="热度任务",
                keyword="MacBook",
                decision_mode="hotness",
                hotness_config=None,
            )

    def test_hotness_mode_with_valid_config(self):
        task = TaskCreate(
            task_name="热度任务",
            keyword="MacBook",
            decision_mode="hotness",
            hotness_config=HotnessConfig(
                collect_per_minute=0.01,
                condition_logic="or",
            ),
        )
        assert task.decision_mode == "hotness"
        assert task.hotness_config is not None
        assert task.hotness_config.collect_per_minute == 0.01

    def test_ai_mode_does_not_require_hotness_config(self):
        task = TaskCreate(
            task_name="AI任务",
            keyword="MacBook",
            description="需要成色好的",
            decision_mode="ai",
        )
        assert task.hotness_config is None

    def test_hotness_config_from_dict(self):
        """hotness_config 可从字典自动转换"""
        task = TaskCreate(
            task_name="热度任务",
            keyword="MacBook",
            decision_mode="hotness",
            hotness_config={
                "collect_per_minute": 0.05,
                "want_per_minute": 0.03,
                "condition_logic": "and",
            },
        )
        assert task.hotness_config.collect_per_minute == 0.05
        assert task.hotness_config.condition_logic == "and"


class TestTaskGenerateRequestHotnessValidation:
    def test_hotness_mode_requires_config(self):
        with pytest.raises(ValueError, match="热度判断模式.*hotness_config"):
            TaskGenerateRequest(
                task_name="热度任务",
                keyword="MacBook",
                decision_mode="hotness",
            )

    def test_hotness_mode_accepts_valid_config(self):
        req = TaskGenerateRequest(
            task_name="热度任务",
            keyword="MacBook",
            decision_mode="hotness",
            hotness_config=HotnessConfig(
                browse_per_minute=0.5,
                condition_logic="or",
            ),
        )
        assert req.hotness_config.browse_per_minute == 0.5


class TestTaskModelHotness:
    def test_task_entity_holds_hotness_config(self):
        task = Task(
            id=1,
            task_name="热度任务",
            enabled=True,
            keyword="MacBook",
            max_pages=3,
            personal_only=True,
            ai_prompt_base_file="prompts/base_prompt.txt",
            ai_prompt_criteria_file="",
            decision_mode="hotness",
            hotness_config=HotnessConfig(
                collect_per_minute=0.02,
                want_per_minute=0.01,
            ),
        )
        assert task.decision_mode == "hotness"
        assert task.hotness_config.collect_per_minute == 0.02
        assert task.hotness_config.want_per_minute == 0.01

    def test_task_apply_update_changes_hotness_config(self):
        from src.domain.models.task import TaskUpdate

        task = Task(
            id=1,
            task_name="热度任务",
            enabled=True,
            keyword="MacBook",
            max_pages=3,
            personal_only=True,
            ai_prompt_base_file="prompts/base_prompt.txt",
            ai_prompt_criteria_file="",
            decision_mode="hotness",
            hotness_config=HotnessConfig(collect_per_minute=0.01),
        )

        update = TaskUpdate(
            hotness_config=HotnessConfig(
                collect_per_minute=0.05,
                browse_per_minute=1.0,
                condition_logic="and",
            ),
        )
        updated = task.apply_update(update)
        # model_copy 可能将嵌套 BaseModel 转为 dict，统一处理
        hc = updated.hotness_config
        if isinstance(hc, dict):
            hc = HotnessConfig(**hc)
        assert hc.collect_per_minute == 0.05
        assert hc.browse_per_minute == 1.0
        assert hc.condition_logic == "and"
