# This directory holds uploaded PDFs and derived artifacts (parsed text,
# chunk index files) produced by the parse_pdf worker task.
#
# Layout: storage/workspaces/{uuid[:2]}/{workspace_id}/artifacts/{token}.<ext>
#
# This directory is gitignored (see root .gitignore) because it contains
# real PDF content. The directory itself is kept in the repo so the path
# exists on fresh clones.
