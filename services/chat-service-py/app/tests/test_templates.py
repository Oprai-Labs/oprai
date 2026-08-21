"""
Tests for Template System module.

Tests prompt template rendering, conditionals, loops, and context management.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestTemplateContext:
    """Test TemplateContext dataclass"""

    def test_context_creation(self):
        """Test creating TemplateContext"""
        from app.templates import TemplateContext

        ctx = TemplateContext(
            character_name="Bot",
            bio="A helpful assistant"
        )

        assert ctx.character_name == "Bot"
        assert ctx.bio == "A helpful assistant"

    def test_context_defaults(self):
        """Test default values"""
        from app.templates import TemplateContext

        ctx = TemplateContext()

        assert ctx.character_name == ""
        assert ctx.bio == ""
        assert ctx.max_tweet_length == 280
        assert ctx.custom == {}

    def test_context_get(self):
        """Test get method"""
        from app.templates import TemplateContext

        ctx = TemplateContext(character_name="TestBot")

        assert ctx.get("character_name") == "TestBot"
        assert ctx.get("nonexistent", "default") == "default"

    def test_context_get_custom(self):
        """Test get method for custom fields"""
        from app.templates import TemplateContext

        ctx = TemplateContext()
        ctx.custom["custom_field"] = "custom_value"

        assert ctx.get("custom_field") == "custom_value"

    def test_context_set(self):
        """Test set method"""
        from app.templates import TemplateContext

        ctx = TemplateContext()
        ctx.set("character_name", "NewBot")

        assert ctx.character_name == "NewBot"

    def test_context_set_custom(self):
        """Test set method for custom fields"""
        from app.templates import TemplateContext

        ctx = TemplateContext()
        ctx.set("new_field", "new_value")

        assert ctx.custom["new_field"] == "new_value"


class TestPromptTemplate:
    """Test PromptTemplate class"""

    def test_template_creation(self):
        """Test creating a template"""
        from app.templates import PromptTemplate

        template = PromptTemplate("Hello {{name}}!", name="greeting")

        assert template.template == "Hello {{name}}!"
        assert template.name == "greeting"

    def test_render_simple(self):
        """Test rendering simple template"""
        from app.templates import PromptTemplate, TemplateContext

        template = PromptTemplate("Hello {{name}}!")
        ctx = TemplateContext(custom={"name": "World"})

        result = template.render(ctx)

        assert result == "Hello World!"

    def test_render_multiple_variables(self):
        """Test rendering multiple variables"""
        from app.templates import PromptTemplate, TemplateContext

        template = PromptTemplate("{{greeting}} {{name}}, you are {{age}} years old.")
        ctx = TemplateContext(custom={"greeting": "Hello", "name": "Alice", "age": "30"})

        result = template.render(ctx)

        assert result == "Hello Alice, you are 30 years old."

    def test_render_missing_variable(self):
        """Test rendering with missing variable"""
        from app.templates import PromptTemplate, TemplateContext

        template = PromptTemplate("Hello {{name}}!")
        ctx = TemplateContext()  # name not set

        result = template.render(ctx)

        assert result == "Hello !"

    def test_render_with_dict(self):
        """Test rendering with dict context"""
        from app.templates import PromptTemplate

        template = PromptTemplate("Hello {{name}}!")
        result = template.render({"name": "World"})

        assert result == "Hello World!"


class TestPromptTemplateConditionals:
    """Test template conditional blocks"""

    def test_conditional_if_true(self):
        """Test {#if} block with true value"""
        from app.templates import PromptTemplate, TemplateContext

        template = PromptTemplate("Hello {#if name}{{name}}{/if}!")
        ctx = TemplateContext(custom={"name": "World"})

        result = template.render(ctx)

        assert result == "Hello World!"

    def test_conditional_if_false(self):
        """Test {#if} block with false value"""
        from app.templates import PromptTemplate, TemplateContext

        template = PromptTemplate("Hello {#if name}{{name}}{/if}!")
        ctx = TemplateContext(custom={"name": ""})

        result = template.render(ctx)

        assert result == "Hello !"

    def test_conditional_if_none(self):
        """Test {#if} block with None value"""
        from app.templates import PromptTemplate, TemplateContext

        template = PromptTemplate("Hello {#if name}{{name}}{/if}!")
        ctx = TemplateContext(custom={"name": None})

        result = template.render(ctx)

        assert result == "Hello !"

    def test_conditional_if_list(self):
        """Test {#if} block with non-empty list"""
        from app.templates import PromptTemplate, TemplateContext

        template = PromptTemplate("{#if items}Has items{/if}")
        ctx = TemplateContext(custom={"items": [1, 2, 3]})

        result = template.render(ctx)

        assert result == "Has items"

    def test_conditional_if_empty_list(self):
        """Test {#if} block with empty list"""
        from app.templates import PromptTemplate, TemplateContext

        template = PromptTemplate("{#if items}Has items{/if}")
        ctx = TemplateContext(custom={"items": []})

        result = template.render(ctx)

        assert result == ""


class TestPromptTemplateLoops:
    """Test template each loops"""

    def test_each_loop(self):
        """Test {#each} loop"""
        from app.templates import PromptTemplate, TemplateContext

        template = PromptTemplate("Items: {#each items as item}{{item}},{/each}")
        ctx = TemplateContext(custom={"items": ["a", "b", "c"]})

        result = template.render(ctx)

        assert "a" in result
        assert "b" in result
        assert "c" in result

    def test_each_loop_empty(self):
        """Test {#each} with empty list"""
        from app.templates import PromptTemplate, TemplateContext

        template = PromptTemplate("Items: {#each items as item}{{item}}{/each}")
        ctx = TemplateContext(custom={"items": []})

        result = template.render(ctx)

        assert result == "Items:"

    def test_each_loop_non_list(self):
        """Test {#each} with non-list value"""
        from app.templates import PromptTemplate, TemplateContext

        template = PromptTemplate("{#each items as item}{{item}}{/each}")
        ctx = TemplateContext(custom={"items": "not a list"})

        result = template.render(ctx)

        assert result == ""


class TestPromptTemplateWhitespace:
    """Test whitespace handling"""

    def test_multiple_newlines(self):
        """Test multiple newlines are collapsed"""
        from app.templates import PromptTemplate, TemplateContext

        template = PromptTemplate("Line1\n\n\n\nLine2")
        ctx = TemplateContext()

        result = template.render(ctx)

        assert "\n\n\n\n" not in result


class TestTemplateManager:
    """Test TemplateManager class"""

    def test_init_default(self):
        """Test initialization with defaults"""
        from app.templates import TemplateManager

        manager = TemplateManager()

        assert manager.templates_dir is None
        assert manager._templates is not None

    def test_init_with_dir(self):
        """Test initialization with templates directory"""
        from pathlib import Path

        from app.templates import TemplateManager

        manager = TemplateManager(templates_dir=Path("/tmp/templates"))

        assert manager.templates_dir == Path("/tmp/templates")

    def test_default_templates_loaded(self):
        """Test default templates are loaded"""
        from app.templates import TemplateManager

        manager = TemplateManager()

        assert "system" in manager.list_templates()
        assert "message_handler" in manager.list_templates()
        assert "twitter_post" in manager.list_templates()

    def test_get_template(self):
        """Test getting a template"""
        from app.templates import TemplateManager

        manager = TemplateManager()
        template = manager.get_template("system")

        assert template is not None
        assert "character_name" in template.template

    def test_get_template_not_found(self):
        """Test getting non-existent template"""
        from app.templates import TemplateManager

        manager = TemplateManager()
        template = manager.get_template("nonexistent")

        assert template is None

    def test_register_template(self):
        """Test registering a new template"""
        from app.templates import TemplateManager

        manager = TemplateManager()
        manager.register_template("custom", "Hello {{name}}!")

        template = manager.get_template("custom")
        assert template is not None

    def test_render(self):
        """Test rendering via manager"""
        from app.templates import TemplateContext, TemplateManager

        manager = TemplateManager()
        result = manager.render("system", TemplateContext(character_name="Bot"))

        assert "Bot" in result

    def test_render_not_found(self):
        """Test rendering non-existent template"""
        from app.templates import TemplateContext, TemplateManager

        manager = TemplateManager()

        with pytest.raises(ValueError, match="not found"):
            manager.render("nonexistent", TemplateContext())

    def test_list_templates(self):
        """Test listing templates"""
        from app.templates import TemplateManager

        manager = TemplateManager()
        templates = manager.list_templates()

        assert isinstance(templates, list)
        assert len(templates) > 0


class TestDefaultTemplates:
    """Test default template strings"""

    def test_default_system_template(self):
        """Test system template contains expected placeholders"""
        from app.templates import DEFAULT_SYSTEM_TEMPLATE

        assert "{{character_name}}" in DEFAULT_SYSTEM_TEMPLATE
        assert "{{bio}}" in DEFAULT_SYSTEM_TEMPLATE
        assert "{{lore}}" in DEFAULT_SYSTEM_TEMPLATE
        assert "{{topics}}" in DEFAULT_SYSTEM_TEMPLATE
        assert "{{adjectives}}" in DEFAULT_SYSTEM_TEMPLATE

    def test_default_message_handler_template(self):
        """Test message handler template"""
        from app.templates import DEFAULT_MESSAGE_HANDLER_TEMPLATE

        assert "{{character_name}}" in DEFAULT_MESSAGE_HANDLER_TEMPLATE
        assert "{{bio}}" in DEFAULT_MESSAGE_HANDLER_TEMPLATE

    def test_default_twitter_post_template(self):
        """Test twitter post template"""
        from app.templates import DEFAULT_TWITTER_POST_TEMPLATE

        assert "{{character_name}}" in DEFAULT_TWITTER_POST_TEMPLATE
        assert "{{twitter_handle}}" in DEFAULT_TWITTER_POST_TEMPLATE
        assert "{{max_tweet_length}}" in DEFAULT_TWITTER_POST_TEMPLATE

    def test_default_should_respond_template(self):
        """Test should respond template"""
        from app.templates import DEFAULT_SHOULD_RESPOND_TEMPLATE

        assert "{{message}}" in DEFAULT_SHOULD_RESPOND_TEMPLATE
        assert "{{conversation}}" in DEFAULT_SHOULD_RESPOND_TEMPLATE


class TestGlobalTemplateManager:
    """Test global template manager singleton"""

    def test_get_template_manager(self):
        """Test getting global template manager"""
        # Reset global
        import app.templates as templates_module
        from app.templates import TemplateManager, get_template_manager
        templates_module._template_manager = None

        manager = get_template_manager()

        assert manager is not None
        assert isinstance(manager, TemplateManager)

    def test_singleton(self):
        """Test singleton behavior"""
        # Reset global
        import app.templates as templates_module
        from app.templates import get_template_manager
        templates_module._template_manager = None

        manager1 = get_template_manager()
        manager2 = get_template_manager()

        assert manager1 is manager2
