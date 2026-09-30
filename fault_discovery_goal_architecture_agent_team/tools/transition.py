from pathlib import Path
import json,subprocess,sys
work=Path(sys.argv[1]).resolve(); target=sys.argv[2]; pack=Path(__file__).resolve().parents[1]
spec=json.loads((pack/'workflow/WORKFLOW_SPEC.json').read_text(encoding='utf-8'))
state_path=work/'state.json'; state=json.loads(state_path.read_text(encoding='utf-8')); cur=state['phase']
expected=spec['transitions'].get(cur)
if target!=expected: raise SystemExit(f'Illegal transition {cur} -> {target}; expected {expected}')
if cur in spec['required_artifacts']:
 subprocess.run([sys.executable,str(pack/'tools/gate_check.py'),str(work),cur],check=True)
state['phase']=target; state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'
',encoding='utf-8')
print('TRANSITION_OK',cur,'->',target)
