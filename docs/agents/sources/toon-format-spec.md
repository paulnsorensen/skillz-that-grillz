# Token-Oriented Object Notation (TOON)

The toon-format project's README and benchmark page (spec v4.1), last verified 2026-09-23, define the TOON encoding and report the vendor claim that TOON matches JSON accuracy with fewer tokens.
Canonical source: [Token-Oriented Object Notation (TOON)](https://github.com/toon-format/toon)

## What the TOON format is

TOON is a lossless encoding of the JSON data model for LLM prompts. It uses YAML-style indentation for nested objects and a CSV-style table for arrays of uniform objects: the header declares the length and field list once, then one row follows per item. For example, `forecast[3]{day,condition}:` precedes three comma-separated rows.

## TOON vendor benchmark claim

The toon-format benchmark reports 72.2% retrieval accuracy for TOON and 71.4% for JSON, with 42.6% fewer tokens, across 244 questions on 4 models. The benchmark prompts use plain JSON, not provider JSON mode; open issue #19 asks for a comparison with structured-output endpoints.

## When the TOON project says not to use TOON

The TOON README says compact JSON often wins for deeply nested or non-uniform data, CSV is smaller for purely tabular data, and some local or quantized models process compact JSON faster despite more tokens.

_Source: https://github.com/toon-format/toon · Updated: 2026-09-23_
