# Compact context protocol

T14 sends only:
- task ID/mode
- integration HEAD
- worktree path
- exact prompt/design/test file paths
- assigned issue IDs
- accepted design slice
- previous verifier receipt on retry

Children may inspect relevant repository files themselves.

Do not send:
- full chat history
- all task reports
- whole artifact tree
- other implementer transcripts

Long output -> artifact file.
Return only compact receipt.
No child may dispatch another child.
