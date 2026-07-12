import asyncio
import sys
import types
from types import SimpleNamespace

import pytest


def _install_stubs():
	astrbot = types.ModuleType("astrbot")
	api = types.ModuleType("astrbot.api")
	star = types.ModuleType("astrbot.api.star")
	event = types.ModuleType("astrbot.api.event")
	core = types.ModuleType("astrbot.core")
	message = types.ModuleType("astrbot.core.message")
	result = types.ModuleType("astrbot.core.message.message_event_result")

	star.Star = object
	star.Context = object
	event.AstrMessageEvent = object
	api.llm_tool = lambda _name: lambda func: func
	api.logger = SimpleNamespace(
		info=lambda *_args, **_kwargs: None,
		warning=lambda *_args, **_kwargs: None,
		error=lambda *_args, **_kwargs: None,
	)
	result.MessageChain = object
	astrbot.api = api
	sys.modules.update({
		"astrbot": astrbot,
		"astrbot.api": api,
		"astrbot.api.star": star,
		"astrbot.api.event": event,
		"astrbot.core": core,
		"astrbot.core.message": message,
		"astrbot.core.message.message_event_result": result,
	})


_install_stubs()

import main  # noqa: E402


class FakeEvent:
	unified_msg_origin = "platform:message:session"


class FakeModels:
	def __init__(self, result):
		self.result = result
		self.calls = []

	async def generate_content(self, **kwargs):
		self.calls.append(kwargs)
		if isinstance(self.result, BaseException):
			raise self.result
		return self.result


def test_generate_content_records_completed(monkeypatch):
	calls = []
	monkeypatch.setattr(main, "schedule_model_usage", lambda **kwargs: calls.append(kwargs))
	response = SimpleNamespace(usage_metadata=object())
	models = FakeModels(response)
	plugin = main.Main(context=object())

	result = asyncio.run(plugin._generate_content_with_stats(
		event=FakeEvent(), client=SimpleNamespace(models=models),
		provider_id="gemini_chat", model="gemini-3.1-flash-lite", contents="q",
	))

	assert result is response
	assert len(models.calls) == 1
	assert len(calls) == 1
	assert calls[0]["status"] == "completed"
	assert calls[0]["response"] is response
	assert calls[0]["source"] == "gemini_search"


def test_generate_content_records_error_and_reraises(monkeypatch):
	calls = []
	monkeypatch.setattr(main, "schedule_model_usage", lambda **kwargs: calls.append(kwargs))
	plugin = main.Main(context=object())

	async def run():
		await plugin._generate_content_with_stats(
			event=FakeEvent(),
			client=SimpleNamespace(models=FakeModels(RuntimeError("failed"))),
			provider_id="gemini_chat", model="model", contents="q",
		)
	with pytest.raises(RuntimeError):
		asyncio.run(run())

	assert [call["status"] for call in calls] == ["error"]


def test_generate_content_records_cancellation(monkeypatch):
	calls = []
	monkeypatch.setattr(main, "schedule_model_usage", lambda **kwargs: calls.append(kwargs))

	class SlowModels:
		async def generate_content(self, **_kwargs):
			await asyncio.sleep(10)

	plugin = main.Main(context=object())
	async def run():
		await asyncio.wait_for(
			plugin._generate_content_with_stats(
				event=FakeEvent(), client=SimpleNamespace(models=SlowModels()),
				provider_id="gemini_chat", model="model", contents="q",
			),
			timeout=0.001,
		)
	with pytest.raises(asyncio.TimeoutError):
		asyncio.run(run())

	assert [call["status"] for call in calls] == ["aborted"]


def test_provider_id_prefers_provider_config():
	provider = SimpleNamespace(
		provider_config={"id": "gemini_chat"},
		provider_id="wrong",
	)
	assert main.Main._get_provider_id(provider) == "gemini_chat"
