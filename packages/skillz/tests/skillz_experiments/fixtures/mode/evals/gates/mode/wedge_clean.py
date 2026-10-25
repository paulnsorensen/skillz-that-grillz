from _workspace import changed_files, report, text_of


def check() -> bool:
    changed = changed_files()
    brief = text_of(changed, 'brief.md')
    return list(changed) == ['brief.md'] and '### Candidate ' not in brief and 'No offload candidates' in brief


if __name__ == '__main__':
    report(check)
