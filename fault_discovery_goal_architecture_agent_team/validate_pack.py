from pathlib import Path
import json,sys
root=Path(__file__).resolve().parent
m=json.loads((root/'MANIFEST.json').read_text(encoding='utf-8'))
required=['ENTRYPOINT_T14.md','workflow/STATE_MACHINE.md','workflow/EVIDENCE_RULES.md','workflow/ANTI_ANCHOR.md','workflow/DEBATE_PROTOCOL.md','workflow/DECISION_PROTOCOL.md','inputs/GOAL_CONTRACT.md','workflow/WORKFLOW_SPEC.json','tools/gate_check.py','tools/transition.py']+list(m['roles'].values())+list(m['rounds'].values())
missing=[x for x in required if not (root/x).exists()]
if missing:
    print('MISSING')
    [print(x) for x in missing]
    sys.exit(1)
spec=json.loads((root/'workflow/WORKFLOW_SPEC.json').read_text(encoding='utf-8'))
assert spec['transitions']['R8_RELEASE_AUDIT']=='DONE'
assert len(list((root/'scenarios').glob('*.md')))>=8
print('PACK_OK roles=%d rounds=%d scenarios=%d final_outputs=%d'%(len(m['roles']),len(m['rounds']),len(list((root/'scenarios').glob('*.md'))),len(m['required_final_outputs'])))
