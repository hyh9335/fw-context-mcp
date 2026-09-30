from pathlib import Path
import json,sys
work=Path(sys.argv[1]).resolve(); phase=sys.argv[2]
pack=Path(__file__).resolve().parents[1]
spec=json.loads((pack/'workflow/WORKFLOW_SPEC.json').read_text(encoding='utf-8'))
if phase not in spec['required_artifacts']: raise SystemExit('Unknown phase '+phase)
missing=[x for x in spec['required_artifacts'][phase] if not (work/x).exists()]
if missing:
 print('GATE_FAIL'); [print('MISSING',x) for x in missing]; raise SystemExit(2)
print('GATE_PASS',phase,len(spec['required_artifacts'][phase]))
