"""Controlled input representation experiment only; never imported by production."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from time import perf_counter

from backend.config.settings import load_settings
from backend.contracts.modules.materials import MaterialInput
from backend.infrastructure.model.factory import open_model
from backend.infrastructure.observability.memory import MemoryEventSink
from backend.infrastructure.parsing.registry import MaterialParserRegistry


async def main(args):
    root = Path(args.workdir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    output = Path(args.output).resolve()
    if output.exists():
        raise ValueError("comparison_output_already_exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    source = Path(args.document).resolve()
    settings = load_settings()
    settings.model.thinking_mode = 'none'
    settings.materials.cache_root = str(root / 'scope-fixture-cache')
    parsed = MaterialParserRegistry(settings.materials).parse(MaterialInput(
        uri=str(source)))
    # Independently inspected source spans establish a controlled input, not
    # an automatic selector or a proposed production task workflow.
    boundaries = json.loads(args.boundaries)
    if (not isinstance(boundaries, list) or len(boundaries) < 2
            or any(type(i) is not int or i < 0 or i > len(parsed.units) for i in boundaries)
            or any(a >= b for a, b in zip(boundaries, boundaries[1:]))):
        raise ValueError("invalid_manually_verified_source_boundaries")
    items = [
        {'ordinal': i + 1, 'source_span': [a, b],
         'text': '\n'.join(u.text for u in parsed.units[a:b] if u.text.strip())}
        for i, (a, b) in enumerate(zip(boundaries, boundaries[1:]))
    ]
    contents = {
        'full': '\n'.join(u.text for u in parsed.units if u.text.strip()),
        'scoped_plain': '\n\n'.join(x['text'] for x in items),
        'scoped_structured': json.dumps({'source': parsed.manifest.name,
                                         'items': items}, ensure_ascii=False),
    }
    rows = []
    async with open_model(settings=settings, events=MemoryEventSink(), role='main') as model:
        for repetition in range(args.repetitions):
            for variant, content in contents.items():
                started = perf_counter()
                response = await model.client.post(
                    settings.model.endpoint,
                    headers={'x-zhouxu-purpose': 'main'},
                    json={
                        'model': settings.model.name,
                        'messages': [
                            {'role': 'system', 'content': settings.prompts['system']},
                            {'role': 'user', 'content': '材料：\n' + content + '\n用户问题：' + args.question},
                        ],
                        'max_tokens': model.context_budget().output_reserved,
                        'temperature': 0,
                        'thinking': {'type': 'disabled'},
                    },
                )
                response.raise_for_status()
                data = response.json()
                row = {'variant': variant, 'repetition': repetition + 1,
                       'seconds': round(perf_counter() - started, 3),
                       'input_chars': len(content),
                       'answer': data['choices'][0]['message'].get('content'),
                       'usage': data.get('usage'),
                       'finish_reason': data['choices'][0]['finish_reason']}
                rows.append(row)
                output.write_text(
                    json.dumps({'method': 'raw model control; manual inspected spans; no legal gold key; no production selector',
                                'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                                'question': args.question, 'boundaries': boundaries,
                                'selected_items': items, 'results': rows},
                               ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps({k: row[k] for k in ['variant','repetition','seconds','finish_reason']}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--document", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--boundaries", required=True,
                        help="JSON array of independently checked parser unit indices")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required: this spends model credit")
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    asyncio.run(main(args))
