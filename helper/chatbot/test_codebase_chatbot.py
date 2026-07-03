# Proprietary and confidential source code.
# Developer: Sunilkumar Pathipati
# Responsibility: Smoke tests for codebase_chatbot indexing and retrieval.
"""Smoke tests for UnitTest_gen RAG chatbot helpers."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from UnitTest_gen.helper.chatbot.codebase_chatbot import (
    _retrieval_status_line,
    build_chatbot_server_command,
    chat_completion_text,
    is_indexable_chat_path,
    retrieve_context,
    unit_test_gen_root,
)
from UnitTest_gen.helper.chatbot import codebase_chatbot_lib as lib

REPO_ROOT = Path(__file__).resolve().parents[3]
CHATBOT_DIR = Path(__file__).resolve().parent


class CodebaseChatbotTest(unittest.TestCase):
    def test_lib_imports_without_unit_test_gen_on_path(self):
        helper_dir = str(CHATBOT_DIR)
        saved_path = list(sys.path)
        saved_modules = {
            name: sys.modules.pop(name)
            for name in list(sys.modules)
            if name in {"codebase_chatbot_lib", "UnitTest_gen.helper.chatbot.codebase_chatbot_lib"}
        }
        try:
            sys.path[:] = [p for p in saved_path if "UnitTest_gen" not in p and p != helper_dir]
            sys.path.insert(0, helper_dir)
            spec = importlib.util.spec_from_file_location(
                "codebase_chatbot_lib_standalone",
                CHATBOT_DIR / "codebase_chatbot_lib.py",
            )
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            self.assertTrue(callable(module.tokenize))
            self.assertTrue(callable(module.configure_chatbot_runtime))
        finally:
            sys.path[:] = saved_path
            sys.modules.update(saved_modules)

    def test_bundle_root_flat_bot_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = Path(tmp) / "bot"
            bot.mkdir()
            (bot / "codebase_chatbot_lib.py").write_text("# stub\n", encoding="utf-8")
            self.assertEqual(bot, lib._bundle_root(bot))

    def test_bundle_root_detects_doc_sibling_for_desktop_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bot"
            (bundle / "doc").mkdir(parents=True)
            chatbot_dir = bundle / "chatbot"
            chatbot_dir.mkdir()
            self.assertEqual(bundle, lib._bundle_root(chatbot_dir))

    def test_bundle_root_detects_in_repo_helper_layout(self):
        chatbot_dir = Path(__file__).resolve().parent
        self.assertEqual(unit_test_gen_root(), lib._bundle_root(chatbot_dir))

    def test_is_full_unit_test_gen_tree(self):
        self.assertTrue(lib.is_full_unit_test_gen_tree(unit_test_gen_root()))

    def test_default_index_root_is_doc_folder(self):
        self.assertEqual(unit_test_gen_root() / "doc", lib.default_index_root())

    def test_resolve_index_root_defaults_to_doc(self):
        scan_root, docs_only = lib.resolve_index_root(None)
        self.assertEqual(lib.default_index_root(), scan_root)
        self.assertTrue(docs_only)

    def test_resolve_index_root_full_unit_test_gen(self):
        scan_root, docs_only = lib.resolve_index_root(str(unit_test_gen_root()))
        self.assertEqual(unit_test_gen_root().resolve(), scan_root)
        self.assertFalse(docs_only)

    def test_is_indexable_docs_only_accepts_markdown_at_root(self):
        root = str(unit_test_gen_root() / "doc")
        path = os.path.join(root, "POC_CASE_STUDY.md")
        self.assertTrue(is_indexable_chat_path(path, root, docs_only=True))

    def test_is_indexable_accepts_doc_markdown(self):
        root = str(unit_test_gen_root())
        path = os.path.join(root, "doc", "POC_CASE_STUDY.md")
        self.assertTrue(is_indexable_chat_path(path, root))

    def test_is_indexable_rejects_log_files(self):
        root = str(unit_test_gen_root())
        path = os.path.join(root, "log", "Foo.testgen.log")
        self.assertFalse(is_indexable_chat_path(path, root))

    def test_is_indexable_accepts_python_under_kotlin(self):
        root = str(unit_test_gen_root())
        path = os.path.join(root, "kotlin", "generator.py")
        self.assertTrue(is_indexable_chat_path(path, root))

    @patch("codebase_chatbot_lib.embeddings_enabled", return_value=False)
    @patch("codebase_chatbot_lib.get_vector", return_value=None)
    def test_retrieve_context_keyword_hit(self, _mock_vector, _mock_embeddings):
        index = {
            "doc/MODULE_TABLE_OF_CONTENTS.md": {
                "class_name": "doc/MODULE_TABLE_OF_CONTENTS.md",
                "path": str(unit_test_gen_root() / "doc" / "MODULE_TABLE_OF_CONTENTS.md"),
                "content": "Module table of contents for UnitTest_gen pipeline_config generator.",
                "vector": None,
            }
        }
        hits = retrieve_context(
            "Where is pipeline_config documented in the module table?",
            index,
            top_k=3,
        )
        self.assertTrue(hits)
        self.assertEqual("doc/MODULE_TABLE_OF_CONTENTS.md", hits[0][0])

    @patch("codebase_chatbot_lib.get_runtime")
    def test_retrieval_status_reports_unreachable_embedding_server(self, mock_get_runtime):
        runtime = mock_get_runtime.return_value
        runtime.embeddings_enabled.return_value = False
        runtime.embedding_base_url = "http://127.0.0.1:8082/v1"
        runtime.is_embedding_server_available.return_value = False
        status = _retrieval_status_line()
        self.assertIn("embedding server unreachable", status)

    @patch("codebase_chatbot_lib.get_runtime")
    def test_chat_completion_text_returns_model_content(self, mock_get_runtime):
        runtime = mock_get_runtime.return_value
        runtime.chat_model = "test-model"
        runtime.chat_client.chat.completions.create.return_value.choices = [
            type("Choice", (), {"message": type("Message", (), {"content": "Hello there."})()})()
        ]
        answer = chat_completion_text([{"role": "user", "content": "hi"}])
        self.assertEqual("Hello there.", answer)
        runtime.chat_client.chat.completions.create.assert_called_once()
        self.assertFalse(runtime.chat_client.chat.completions.create.call_args.kwargs.get("stream"))

    def test_build_chatbot_server_command_uses_model_and_port(self):
        command = build_chatbot_server_command()
        self.assertIn("llama-3.2-3b-instruct-q4_k_m.gguf", command)
        self.assertIn("--port 8082", command)
        self.assertIn("--ctx-size 16384", command)
        self.assertNotIn("--embedding", command)

    def test_build_chatbot_embedding_server_command_uses_nomic(self):
        command = lib.build_chatbot_embedding_server_command()
        self.assertIn("nomic-embed-text", command)
        self.assertIn("--port 8081", command)
        self.assertIn("--embedding", command)

    def test_configure_runtime_uses_separate_embedding_url(self):
        lib.configure_chatbot_runtime()
        runtime = lib.get_runtime()
        self.assertEqual("http://127.0.0.1:8082/v1", runtime.chat_base_url)
        self.assertEqual("http://127.0.0.1:8081/v1", runtime.embedding_base_url)
        self.assertEqual("nomic-embed-text", runtime.embedding_model)

    def test_knowledge_bundle_roundtrip(self):
        index = {
            "doc/MODULE_TABLE_OF_CONTENTS.md": {
                "class_name": "doc/MODULE_TABLE_OF_CONTENTS.md",
                "path": "doc/MODULE_TABLE_OF_CONTENTS.md",
                "content": "MODEL_STUCK_DETECTOR_ENABLED controls stuck detection.",
                "vector": [0.1, 0.2, 0.3],
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "knowledge.bundle.json"
            lib.export_knowledge_bundle(index, bundle)
            loaded = lib.load_knowledge_bundle(bundle)
        self.assertEqual(1, len(loaded))
        self.assertIn("MODEL_STUCK_DETECTOR_ENABLED", loaded["doc/MODULE_TABLE_OF_CONTENTS.md"]["content"])
        self.assertEqual([0.1, 0.2, 0.3], loaded["doc/MODULE_TABLE_OF_CONTENTS.md"]["vector"])

    def test_build_arg_parser_has_gui_flag(self):
        from UnitTest_gen.helper.chatbot.codebase_chatbot import build_arg_parser

        parser = build_arg_parser()
        self.assertTrue(parser.parse_args(["--gui"]).gui)

    def test_parse_gui_args_injects_gui_flag(self):
        from UnitTest_gen.helper.chatbot.codebase_chatbot_gui import parse_gui_args

        args = parse_gui_args(["--reindex", "--no-manage-server"])
        self.assertTrue(args.gui)
        self.assertTrue(args.reindex)
        self.assertTrue(args.no_manage_server)

    def test_parse_gui_args_leaves_help_alone(self):
        from UnitTest_gen.helper.chatbot.codebase_chatbot_gui import parse_gui_args

        with self.assertRaises(SystemExit) as ctx:
            parse_gui_args(["-h"])
        self.assertEqual(0, ctx.exception.code)

    def test_run_gui_module_imports(self):
        from UnitTest_gen.helper.chatbot import codebase_chatbot_gui

        self.assertTrue(callable(codebase_chatbot_gui.run_gui))
        self.assertTrue(callable(codebase_chatbot_gui.main))

    @patch("UnitTest_gen.helper.chatbot.codebase_chatbot.ensure_managed_servers", return_value=None)
    @patch("UnitTest_gen.helper.chatbot.codebase_chatbot.is_embedding_server_available", return_value=True)
    @patch("UnitTest_gen.helper.chatbot.codebase_chatbot.is_model_server_available", return_value=True)
    @patch("UnitTest_gen.helper.chatbot.codebase_chatbot.build_chat_index", return_value={})
    @patch("builtins.input", side_effect=["quit"])
    def test_main_defaults_to_doc_index_root(
        self,
        _mock_input,
        mock_index,
        _mock_available,
        _mock_embedding_available,
        mock_ensure,
    ):
        from UnitTest_gen.helper.chatbot.codebase_chatbot import main

        exit_code = main(["--no-manage-server"])

        self.assertEqual(0, exit_code)
        mock_ensure.assert_called_once()
        self.assertFalse(mock_ensure.call_args.kwargs["manage"])
        mock_index.assert_called_once()
        self.assertEqual(str(lib.default_index_root()), mock_index.call_args.args[0])
        self.assertTrue(mock_index.call_args.kwargs.get("docs_only"))

    @patch("UnitTest_gen.helper.chatbot.codebase_chatbot.ensure_managed_servers")
    @patch("UnitTest_gen.helper.chatbot.codebase_chatbot.is_embedding_server_available", side_effect=[False, True, True])
    @patch("UnitTest_gen.helper.chatbot.codebase_chatbot.is_model_server_available", side_effect=[False, True, True])
    @patch("UnitTest_gen.helper.chatbot.codebase_chatbot.build_chat_index", return_value={})
    @patch("builtins.input", side_effect=["quit"])
    def test_main_starts_and_stops_managed_server(
        self,
        _mock_input,
        _mock_index,
        _mock_available,
        _mock_embedding_available,
        mock_ensure,
    ):
        from unittest.mock import MagicMock

        from UnitTest_gen.helper.chatbot.codebase_chatbot import main

        manager = MagicMock()
        mock_ensure.return_value = manager

        exit_code = main(["--no-server-terminal"])

        self.assertEqual(0, exit_code)
        mock_ensure.assert_called_once()
        manager.stop_all_started_servers.assert_called_once()

    @patch.object(lib.ChatbotServerManager, "wait_until", return_value=True)
    @patch.object(lib.ChatbotServerManager, "start_embedding_server")
    @patch.object(lib.ChatbotServerManager, "start_coding_server")
    @patch.object(lib, "is_embedding_server_available", side_effect=[False, True])
    @patch.object(lib, "is_model_server_available", side_effect=[False, True])
    @patch.object(lib, "embeddings_requested", return_value=True)
    def test_ensure_managed_servers_starts_both_when_unreachable(
        self,
        _mock_embeddings_requested,
        _mock_chat_available,
        _mock_embed_available,
        mock_start_chat,
        mock_start_embed,
        _mock_wait,
    ):
        manager = lib.ensure_managed_servers(
            manage=True,
            use_terminal=False,
            project_root=str(unit_test_gen_root()),
        )

        self.assertIsInstance(manager, lib.ChatbotServerManager)
        mock_start_chat.assert_called_once()
        mock_start_embed.assert_called_once()


if __name__ == "__main__":
    unittest.main()
