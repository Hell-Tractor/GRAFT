from pathlib import Path
import tempfile
import unittest

from experiment_batch import model_config, read_key


class EntryTests(unittest.TestCase):
    def test_env_file_is_parsed_as_data_not_executed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = root/'key.env'
            marker = root/'must-not-exist'
            env.write_text(f'export DEEPSEEK_API_KEY="$(touch {marker})"\n')
            self.assertTrue(read_key(env).startswith('$(touch '))
            self.assertFalse(marker.exists())
            env.write_text("export DEEPSEEK_API_KEY='fake-unit-test-key' # test\n")
            self.assertEqual(read_key(env), 'fake-unit-test-key')

    def test_missing_or_duplicate_key_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            env = Path(temp)/'key.env'
            for text in ['', 'DEEPSEEK_API_KEY=\n', 'DEEPSEEK_API_KEY=a\nDEEPSEEK_API_KEY=b\n']:
                env.write_text(text)
                with self.assertRaises(ValueError):
                    read_key(env)

    def test_task_config_has_one_token_limit_and_no_copied_secret(self):
        original = 'LLM:\n  type: "DeepSeek"\n  model_name: "old"\n  api_url_chat: "old"\n  max_tokens: 32768\n  api_key: "fake-unit-secret"\n  enable_thinking: true\n  thinking_budget: 10\n  fix_retries: 3\ncommands:\n  cargo: "cargo"\n'
        config = model_config(original, 'deepseek-v4-pro', 'https://api.deepseek.com/chat/completions')
        self.assertEqual(config.count('max_tokens:'), 1)
        self.assertIn('max_tokens: 65536', config)
        self.assertNotIn('fake-unit-secret', config)
        self.assertNotIn('enable_thinking:', config)
        self.assertIn('fix_retries: 3', config)
        self.assertIn('commands:', config)


if __name__ == '__main__':
    unittest.main()
