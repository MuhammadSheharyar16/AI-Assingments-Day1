# Day 9 Ontology Requirements

The supplied registry is a synthetic Mode-A registry.

The implementation must load it through typed models.

Validate:

- ontology version required
- unique concept IDs
- unique intent IDs
- relationship targets exist
- intent lane IDs are governed
- status values are valid
- runtime registry is read-only

Every Gate-A / lane decision must expose the ontology version used.

## Important

The ontology is control metadata.

It does not contain supplier facts and does not replace Mode B evidence.
