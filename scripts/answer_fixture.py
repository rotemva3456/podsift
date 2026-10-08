"""Short-lived browser fixture. No model inference; never used by dev.sh."""
import os
import sys
import tempfile
import time
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.answers import AnswerClaim, ProviderAnswer, ProviderError
from companion.server import create_app


class BrowserFixtureProvider:
    def answer(self, context):
        if context.question == 'Simulate a service failure':
            raise ProviderError('The answer service is unavailable. Please try again.')
        if context.question == 'Simulate insufficient evidence':
            return ProviderAnswer(status='insufficient_evidence', claims=[])
        if context.question == 'Simulate a slow answer':
            time.sleep(2)
        source = next((p for p in context.passages if p.end is not None
                       and p.start <= context.position < p.end), context.passages[0])
        citation = 'invented-source' if context.question == 'Simulate an invalid citation' else source.id
        return ProviderAnswer(status='answered', claims=[AnswerClaim(
            text='Browser fixture excerpt: ' + source.text, citations=[citation])])


if __name__ == '__main__':
    library = Path(os.getenv('PODCAST_LIBRARY', ROOT / 'operator/library'))
    with tempfile.TemporaryDirectory(prefix='podcast-answer-browser-') as temporary:
        app = create_app('http://127.0.0.1:18080', Path(temporary) / 'notes.db',
                         library if library.is_dir() else None,
                         answer_provider=BrowserFixtureProvider())
        uvicorn.run(app, host='127.0.0.1', port=int(os.getenv('ANSWER_FIXTURE_PORT', '18182')))
