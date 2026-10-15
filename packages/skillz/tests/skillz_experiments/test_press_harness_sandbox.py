'''Press attacks on harness-sandbox-ergonomics (AC-1 to AC-15): version parsing, resume stops, usage errors, Gemini wrapper.'''
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast, final

import pytest

from skillz_experiments import _cli
from skillz_experiments.run import _run, _session
from skillz_experiments._cases import CodedError
from skillz_experiments._cli import main
from skillz_experiments.harness import Configuration, _doctor
from skillz_experiments.harness._check import MISSING_CREDENTIAL, SUBSCRIPTION_LOGIN
from skillz_experiments.harness._codex import Codex, supported
from skillz_experiments.wrapper import _gemini
from skillz_experiments._records import read, write
from skillz_experiments._budget import Budget
from skillz_experiments.run import Stop, run
from typing_extensions import override
from fake_provider import FakeProvider

FIXTURES = Path(__file__).parent / 'fixtures'
REPO = Path(__file__).resolve().parents[4]
WRAPPER_MAIN = 'import sys; from skillz_experiments._cli import main; sys.exit(main())'


# ---------------------------------------------------------------- AC-1 / AC-2 version parsing

@pytest.mark.parametrize('banner', [
    'codex-cli 0.154.0+build', 'codex-cli 0.154.0+build.5-x', 'codex-cli 0.1540.0', 'codex-cli 0.160.0',
    'codex-cli 1.0.0', '  codex-cli 0.160.0  ', '\ncodex-cli 0.160.0\n', 'codex-cli 0.155.0-alpha.1',
    'codex-cli 99999999999999999999.0.0',
])
def test_supported_acceptsEveryReleaseAtOrAboveTheMinimum(banner: str) -> None:
    assert supported(banner) is True


@pytest.mark.parametrize('banner', [
    'codex-cli 0.154.0-alpha.1', 'codex-cli 0.154', 'codex-cli 0.154.0.1', 'codex-cli 0.153.99999', 'codex-cli 0.9.9',
    'codex-cli -1.154.0', 'codex-cli 1.-154.0', 'codex-cli 1.154.-0', 'codex-cli +1.154.0', 'codex-cli 1.154.0-',
    'codex-cli 1.154.0+', 'codex-cli  1.154.0', 'codex-cli\t1.154.0', 'Codex-CLI 1.154.0', 'codex-cli 1.154.0 extra',
    'codex-cli 1.154.0\ncodex-cli 1.154.0', 'codex-cli 1.154.0\nwarning: update', 'warning\ncodex-cli 1.154.0',
    'codex-cli v1.154.0', 'codex-cli 1.154.0' + chr(0), 'codex-cli 1_0.154.0', 'codex-cli 1,154,0',
    '', '   ', 'codex-cli', 'codex-cli ..', 'codex-cli 1..0',
])
def test_supported_rejectsMalformedOrOlderBanners(banner: str) -> None:
    assert supported(banner) is False


@pytest.mark.parametrize('banner', ['codex-cli ٠.١٥٤.٠', 'codex-cli ١.٠.٠', 'codex-cli ０.１６０.０'])
def test_supported_nonAsciiDigits_areUnparsable(banner: str) -> None:
    '''AC-2: a version written in non-ASCII digits is unparsable and must be refused. The regex digit class and int() accept it.'''
    assert supported(banner) is False


def test_supported_digitStringBeyondIntLimit_doesNotRaise() -> None:
    '''AC-2: an absurd version stays a verdict (a bool), never an uncoded ValueError from int().'''
    assert isinstance(supported('codex-cli ' + '9' * 5000 + '.0.0'), bool)


def test_preflight_absurdVersion_stopsWithUnsupportedAfterOnlyTheVersionCall(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    '''AC-2: the version gate must not leak a bare ValueError from int() on a 5000-digit component.'''
    if Path('/etc/codex/skills').exists():
        pytest.skip('admin skill roots stop the Codex transport')
    fake = tmp_path / 'bin/codex'
    fake.parent.mkdir()
    log = tmp_path / 'calls.log'
    _ = fake.write_text(f'#!{sys.executable}\nimport sys\nopen({str(log)!r}, "a").write(" ".join(sys.argv[1:]) + "\\n")\nprint("codex-cli " + "9" * 5000 + ".0.0") if sys.argv[1:] == ["--version"] else None\n')
    fake.chmod(0o755)
    monkeypatch.setenv('PATH', f'{fake.parent}:{os.environ["PATH"]}')
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'host'))
    _ = (tmp_path / 'host').mkdir()
    _ = (tmp_path / 'host/auth.json').write_text('{"tokens": {"access_token": "t"}}')
    codex = Codex('m', Budget(10, 120, 0), lambda: None)
    try:
        with pytest.raises(CodedError) as caught:
            _ = codex.preflight()
        assert caught.value.code == 'codex-version-unsupported'
    finally:
        codex.close()
    assert log.read_text().splitlines() == ['--version']


# ---------------------------------------------------------------- AC-4 resume on another Codex version

@final
class _Calls:
    def __init__(self) -> None:
        self.models = 0
        self.version = 'codex-cli 0.160.0'


@pytest.fixture
def codex_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Calls:
    if Path('/etc/codex/skills').exists():
        pytest.skip('admin skill roots stop the Codex transport')
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'host'))
    calls = _Calls()

    def preflight(self: Codex) -> dict[str, object]:
        return {'codex_version': calls.version, 'model': 'm', 'isolation': 'passed', 'credential': self.credential_kind,
                'live_calls': 0, 'environment_hash': 'h'}

    def check_candidate(self: Codex, candidate: object) -> bool:
        del self, candidate
        return True

    def evaluate(self: Codex, candidate: object, case: object, *, holdout: bool = False) -> dict[str, object]:
        del self, candidate, case, holdout
        calls.models += 1
        raise RuntimeError('model call reached')

    def invoke(self: Codex, *args: object, **kwargs: object) -> dict[str, object]:
        del self, args, kwargs
        calls.models += 1
        raise RuntimeError('model call reached')

    monkeypatch.setattr(Codex, 'preflight', preflight)
    monkeypatch.setattr(Codex, 'check_candidate', check_candidate)
    monkeypatch.setattr(Codex, 'evaluate', evaluate)
    monkeypatch.setattr(Codex, 'invoke', invoke)
    return calls


def _codex_prepared(tmp_path: Path, target: Path, write_draft: Callable[..., list[str]], calls: _Calls) -> Path:
    out = tmp_path / 'run'
    _ = write_draft(out)
    with pytest.raises(Stop) as cases:
        _ = run(target, out, 'm', adapter='codex', live=True)
    case_hash = cast(str, cases.value.data['case_hash'])
    with pytest.raises(Stop) as budget:
        _ = run(target, out, 'm', adapter='codex', live=True, approve_cases=case_hash)
    count = cast(dict[str, int], budget.value.data['estimate'])['calls']
    with pytest.raises(CodedError, match='model call reached'):
        _ = run(target, out, 'm', adapter='codex', live=True, approve_cases=case_hash, approve_budget=count)
    assert calls.models >= 1
    assert read(out / 'run.json')['codex_version'] == 'codex-cli 0.160.0'
    return out


def test_codexResume_onAnotherVersion_stopsBeforeAnyModelCall(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        codex_world: _Calls) -> None:
    '''AC-4: the environment-hash stop names the changed evidence key and both versions.'''
    target = make_target(tmp_path / 'source')
    out = _codex_prepared(tmp_path, target, write_draft, codex_world)
    before = codex_world.models
    codex_world.version = 'codex-cli 0.161.0'
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, 'm', adapter='codex', live=True)
    assert caught.value.code == 'environment-differs'
    assert 'codex_version' in str(caught.value)
    assert 'codex-cli 0.160.0' in str(caught.value)
    assert 'codex-cli 0.161.0' in str(caught.value)
    assert codex_world.models == before


def test_codexResume_onTheRecordedVersion_isNotAnEnvironmentStop(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        codex_world: _Calls) -> None:
    target = make_target(tmp_path / 'source')
    out = _codex_prepared(tmp_path, target, write_draft, codex_world)
    with pytest.raises(CodedError, match='model call reached') as caught:
        _ = run(target, out, 'm', adapter='codex', live=True)
    assert caught.value.code == 'search-faulted'


def test_codexRun_recordsTheCredentialKindOfEveryBuiltInRoleInRunJson(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        codex_world: _Calls) -> None:
    target = make_target(tmp_path / 'source')
    _ = (tmp_path / 'host').mkdir()
    _ = (tmp_path / 'host/auth.json').write_text('{"tokens": {"access_token": "t"}}')
    out = _codex_prepared(tmp_path, target, write_draft, codex_world)
    roles = cast(dict[str, dict[str, object]], cast(dict[str, dict[str, object]], read(out / 'run.json')['preflight'])['roles'])
    assert {name: role['credential'] for name, role in roles.items()} == {
        'task': SUBSCRIPTION_LOGIN, 'reflection': SUBSCRIPTION_LOGIN, 'judge': SUBSCRIPTION_LOGIN}


# ---------------------------------------------------------------- AC-6 nono records

@final
class _Provider(FakeProvider):
    def __init__(self) -> None:
        self.calls = 0

    @override
    def preflight(self) -> dict[str, object]:
        self.calls += 1
        return {'environment_hash': 'e1', 'isolation': 'passed'}

    def evaluate(self, candidate: object, case: object, *, holdout: bool = False,
                 checks: object = None) -> dict[str, object]:
        del candidate, case, holdout, checks
        self.calls += 1
        raise RuntimeError('stop here')


def _factory(provider: _Provider) -> _session.Factory:
    def make(_model: str, _budget: object, _checkpoint: object) -> _Provider:
        return provider
    return cast(_session.Factory, make)


def _prepared_run(tmp_path: Path, target: Path, write_draft: Callable[..., list[str]]) -> Path:
    out = tmp_path / 'run'
    _ = write_draft(out)
    factory = _factory(_Provider())
    with pytest.raises(Stop) as cases:
        _ = run(target, out, 'm', live=True, factory=factory)
    case_hash = cast(str, cases.value.data['case_hash'])
    with pytest.raises(Stop) as budget:
        _ = run(target, out, 'm', live=True, approve_cases=case_hash, factory=factory)
    count = cast(dict[str, int], budget.value.data['estimate'])['calls']
    with pytest.raises(CodedError):
        _ = run(target, out, 'm', live=True, approve_cases=case_hash, approve_budget=count, factory=factory)
    return out


@pytest.mark.parametrize('state', ['prepared', 'searched', 'gated', 'unknown-phase'])
def test_nonoRecord_inAnyPhase_stopsWithNonoRemovedAndTheIssueLink(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]], state: str) -> None:
    '''AC-6: the refusal comes first, whatever the recorded phase.'''
    target = make_target(tmp_path / 'source')
    out = _prepared_run(tmp_path, target, write_draft)
    record = read(out / 'run.json')
    record['isolation'] = 'nono'
    record['phase'] = state
    write(out / 'run.json', record)
    provider = _Provider()
    with pytest.raises(CodedError, match='issues/211') as caught:
        _ = run(target, out, 'm', live=True, factory=_factory(provider))
    assert caught.value.code == 'nono-removed' and provider.calls == 0


def test_nonoRecord_afterACompletedRun_returnsTheSummary(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]]) -> None:
    '''A finished run needs no backend, so a nono record still reads back its summary.'''
    target = make_target(tmp_path / 'source')
    out = _prepared_run(tmp_path, target, write_draft)
    record = read(out / 'run.json')
    record['isolation'] = 'nono'
    record['phase'] = 'complete'
    write(out / 'run.json', record)
    provider = _Provider()
    result = run(target, out, 'm', live=True, factory=_factory(provider))
    assert result['phase'] == 'complete' and provider.calls == 0


@pytest.mark.parametrize('damage', ['no-model', 'no-contract', 'bad-statistics', 'null-budget', 'empty-record'])
def test_nonoRecord_withMalformedFields_stillStopsWithNonoRemoved(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]], damage: str) -> None:
    target = make_target(tmp_path / 'source')
    out = _prepared_run(tmp_path, target, write_draft)
    record = read(out / 'run.json')
    if damage == 'empty-record':
        record = {'schema_version': record['schema_version']}
    elif damage == 'no-model':
        _ = record.pop('model')
    elif damage == 'no-contract':
        _ = record.pop('contract')
    elif damage == 'bad-statistics':
        record['statistics'] = 'garbage'
    else:
        record['budget'] = None
    record['isolation'] = 'nono'
    write(out / 'run.json', record)
    with pytest.raises(CodedError) as caught:
        _ = run(target, out, 'm', live=True, factory=_factory(_Provider()))
    assert caught.value.code == 'nono-removed' and 'issues/211' in str(caught.value)


def test_nonoRecord_viaTheCli_exitsNonzeroWithTheCodeOnStderr(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        capsys: pytest.CaptureFixture[str]) -> None:
    target = make_target(tmp_path / 'source')
    out = _prepared_run(tmp_path, target, write_draft)
    record = read(out / 'run.json')
    record['isolation'] = 'nono'
    write(out / 'run.json', record)
    _ = capsys.readouterr()
    code = main(['run', '--target', str(target), '--out', str(out), '--model', 'm', '--edit', 'prose', '--live'])
    err = cast(dict[str, str], json.loads(capsys.readouterr().err))
    assert code == 1 and err['code'] == 'nono-removed' and 'issues/211' in err['error']


# ---------------------------------------------------------------- AC-9 / AC-10 / AC-11 usage errors

def _wrapper(tmp_path: Path, mode: str = 'normal') -> tuple[Path, Path, Path]:
    wrapper = tmp_path / 'wrapper.py'
    _ = shutil.copyfile(FIXTURES / 'command_wrapper.py', wrapper)
    settings = tmp_path / 'settings.json'
    log = tmp_path / 'requests.jsonl'
    _ = settings.write_text(json.dumps({'mode': mode, 'log': str(log)}))
    return wrapper, settings, log


def _inline(tmp_path: Path, mode: str = 'normal') -> tuple[list[str], Path, Path]:
    wrapper, settings, log = _wrapper(tmp_path, mode)
    return (['--harness', 'command', *[f'--command={part}' for part in (sys.executable, str(wrapper), str(settings))]],
            wrapper, log)


def _config(tmp_path: Path, mode: str = 'normal') -> tuple[Path, Path, Path]:
    wrapper, settings, log = _wrapper(tmp_path, mode)
    config = tmp_path / 'harness.json'
    _ = config.write_text(json.dumps({'schema_version': 1, 'adapter': 'command', 'identity': 'fixture',
                                      'command': [sys.executable, str(wrapper), str(settings)]}))
    return config, wrapper, log


def _ops(log: Path) -> list[str]:
    if not log.exists():
        return []
    return [cast(str, json.loads(line)['operation']) for line in log.read_text().splitlines()]


def _cli_run(capsys: pytest.CaptureFixture[str], target: Path, out: Path, *extra: str) -> tuple[int, str, dict[str, object]]:
    code = main(['run', '--target', str(target), '--out', str(out), '--model', 'offline', '--edit', 'prose', '--live', *extra])
    captured = capsys.readouterr()
    return code, captured.out, cast(dict[str, object], json.loads(captured.err)) if captured.err else {}


@pytest.fixture
def target(tmp_path: Path, make_target: Callable[..., Path]) -> Path:
    return make_target(tmp_path / 'source')


def test_inlineCommand_withRepeatedDashLeadingElements_buildsOneArgv(
        tmp_path: Path, target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    '''AC-10: repeated --command=X options, some dash-leading, form one argv and are not read as run options.'''
    seen: list[Configuration | None] = []

    def fake(*args: object, **kwargs: object) -> dict[str, object]:
        del args
        seen.append(cast('Configuration | None', kwargs['configuration']))
        return {}

    monkeypatch.setattr(_cli, 'run_workflow', fake)
    out = tmp_path / 'run'
    code = main(['run', '--target', str(target), '--out', str(out), '--model', 'm', '--edit', 'prose', '--harness', 'command',
                 f'--command={sys.executable}', '--command=-I', '--command=-c', '--command=--version'])
    assert code == 0 and seen[0] is not None
    assert seen[0].roles['task'].command[1:] == ('-I', '-c', '--version')
    assert Path(seen[0].roles['task'].command[0]).is_absolute()


@pytest.mark.parametrize('flags', [
    ['--harness', 'command', '--command='],
    ['--harness', 'command', '--command=/definitely/not/installed/harness'],
    ['--harness', 'command', '--command=definitely-not-installed-harness', '--command=-I'],
    ['--harness', 'command', '--command=/tmp'],
    ['--harness', 'command', '--command=' + 'a' * 5000],
    ['--harness', 'command'],
    ['--command=python3'],
    ['--harness', 'claude', '--command=python3'],
    ['--harness', 'codex', '--command=python3'],
])
def test_runUsageErrors_exitNonzeroAsJsonAndCreateNothingUnderOut(
        tmp_path: Path, target: Path, capsys: pytest.CaptureFixture[str], flags: list[str]) -> None:
    out = tmp_path / 'run'
    code, _, err = _cli_run(capsys, target, out, *flags)
    assert code != 0 and 'error' in err
    assert not out.exists()


@pytest.mark.parametrize('kind', ['missing', 'directory', 'empty', 'binary', 'array', 'null', 'bad-version', 'unknown-role',
                                  'nono-key'])
def test_harnessConfig_thatCannotLoad_exitsNonzeroAsJsonAndCreatesNothingUnderOut(
        tmp_path: Path, target: Path, capsys: pytest.CaptureFixture[str], kind: str) -> None:
    config = tmp_path / 'cfg.json'
    if kind == 'directory':
        config.mkdir()
    elif kind == 'empty':
        _ = config.write_text('')
    elif kind == 'binary':
        _ = config.write_bytes(bytes([255, 254, 0, 1]))
    elif kind == 'array':
        _ = config.write_text('[]')
    elif kind == 'null':
        _ = config.write_text('null')
    elif kind == 'bad-version':
        _ = config.write_text(json.dumps({'schema_version': 2}))
    elif kind == 'unknown-role':
        _ = config.write_text(json.dumps({'schema_version': 1, 'roles': {'extra': {}}}))
    elif kind == 'nono-key':
        _ = config.write_text(json.dumps({'schema_version': 1, 'adapter': 'codex', 'isolation': 'nono'}))
    out = tmp_path / 'run'
    code, _, err = _cli_run(capsys, target, out, '--harness-config', str(config))
    assert code != 0 and 'error' in err and not out.exists()


@pytest.mark.parametrize('via', ['symlinked-config', 'relative-script', 'relative-executable'])
def test_commandOrConfig_reachingIntoTheTarget_isRefusedBeforeWork(
        tmp_path: Path, target: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, via: str) -> None:
    out = tmp_path / 'run'
    if via == 'symlinked-config':
        config, _, log = _config(tmp_path)
        inside = target / 'harness.json'
        _ = shutil.move(str(config), inside)
        link = tmp_path / 'link.json'
        link.symlink_to(inside)
        flags = ['--harness-config', str(link)]
    else:
        wrapper, settings, log = _wrapper(tmp_path)
        inner = Path(shutil.copyfile(wrapper, target / 'wrapper.py'))
        monkeypatch.chdir(target)
        if via == 'relative-script':
            flags = ['--harness', 'command', f'--command={sys.executable}', '--command=wrapper.py', f'--command={settings}']
        else:
            _ = inner.write_text(f'#!{sys.executable}\n' + inner.read_text())
            inner.chmod(0o755)
            flags = ['--harness', 'command', '--command=./wrapper.py', f'--command={settings}']
    code, _, err = _cli_run(capsys, target, out, *flags)
    # A symlinked config is refused earlier by the record reader (unsafe); either refusal is safe.
    refused = 'outside the candidate' in str(err['error']) or (via == 'symlinked-config' and 'unsafe' in str(err['error']))
    assert code != 0 and refused
    assert not out.exists() and _ops(log) == []


# ---------------------------------------------------------------- AC-12 command role

@final
class _Driver:
    def __init__(self, capsys: pytest.CaptureFixture[str], target: Path, out: Path, harness: list[str]) -> None:
        self.capsys, self.target, self.out, self.harness = capsys, target, out, harness

    def call(self, *extra: str) -> tuple[int, dict[str, object], dict[str, object]]:
        code = main(['run', '--target', str(self.target), '--out', str(self.out), '--model', 'offline', '--edit', 'prose', *self.harness,
                     '--live', *extra])
        captured = self.capsys.readouterr()
        return (code, cast(dict[str, object], json.loads(captured.out)) if captured.out else {},
                cast(dict[str, object], json.loads(captured.err)) if captured.err else {})

    def finish(self) -> tuple[int, dict[str, object], dict[str, object]]:
        _, cases, _ = self.call()
        case_hash = cast(str, cases['case_hash'])
        _, budget, _ = self.call('--approve-cases', case_hash)
        calls = str(cast(dict[str, int], budget['estimate'])['calls'])
        return self.call('--approve-cases', case_hash, '--approve-budget', calls)


PROBE_FAILURES = {
    'exit-3': 'sys.exit(3)',
    'garbage-stdout': "print('not json')",
    'bad-envelope': "print(json.dumps({'schema_version': 1, 'operation': 'sandbox'}))",
    'probe-not-ok': "print(json.dumps({'schema_version': 1, 'operation': 'sandbox', 'returncode': 0, 'stdout': 'nope', 'stderr': ''}))",
    'probe-nonzero': "print(json.dumps({'schema_version': 1, 'operation': 'sandbox', 'returncode': 1, 'stdout': 'isolation-ok', 'stderr': ''}))",
    'wrong-op': "print(json.dumps({'schema_version': 1, 'operation': 'infer'}))",
    'string-version': "print(json.dumps({'schema_version': '1', 'operation': 'sandbox', 'returncode': 0, 'stdout': 'isolation-ok', 'stderr': ''}))",
    'bool-returncode': "print(json.dumps({'schema_version': 1, 'operation': 'sandbox', 'returncode': False, 'stdout': 'isolation-ok', 'stderr': ''}))",
    'extra-field': "print(json.dumps({'schema_version': 1, 'operation': 'sandbox', 'returncode': 0, 'stdout': 'isolation-ok', 'stderr': '', 'x': 1}))",
}


@pytest.mark.parametrize('failure', sorted(PROBE_FAILURES))
def test_commandRole_sandboxProbeFailure_leavesZeroTaskCalls(
        tmp_path: Path, target: Path, write_draft: Callable[..., list[str]], capsys: pytest.CaptureFixture[str],
        failure: str) -> None:
    script = tmp_path / 'bad.py'
    log = tmp_path / 'ops.log'
    _ = script.write_text('import json, sys\nreq = json.load(sys.stdin)\n'
                          + f"open({str(log)!r}, 'a').write(req['operation'] + ' ')\n" + PROBE_FAILURES[failure] + '\n')
    out = tmp_path / 'run'
    _ = write_draft(out)
    harness = ['--harness', 'command', f'--command={sys.executable}', f'--command={script}']
    code, _, err = _Driver(capsys, target, out, harness).finish()
    assert code != 0 and 'error' in err
    assert log.read_text().split() == ['sandbox']
    assert read(out / 'run.json')['phase'] == 'prepared'


def test_commandRole_neverRunsDoctorEvenWhenClaudeAndCodexAreAbsent(
        tmp_path: Path, target: Path, write_draft: Callable[..., list[str]], capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch) -> None:
    '''AC-12: the real doctor, with claude and codex off PATH, must not run for or block a command role.'''
    empty = tmp_path / 'empty-bin'
    empty.mkdir()
    visits: list[str] = []

    def spying(selections: Sequence[tuple[str, str | None]]) -> dict[str, object]:
        visits.extend(harness for harness, _ in selections)
        return _doctor.host_report(selections)

    monkeypatch.setattr(_run, 'host_report', spying)
    monkeypatch.setenv('PATH', str(empty))
    harness, _, log = _inline(tmp_path)
    out = tmp_path / 'run'
    _ = write_draft(out)
    code, result, _ = _Driver(capsys, target, out, harness).finish()
    assert code == 0 and result['phase'] == 'complete' and visits == []
    assert 'sandbox' in _ops(log)


def test_commandRole_withoutADraft_asksForCasesNotForAHostFix(
        tmp_path: Path, target: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / 'empty-bin'
    empty.mkdir()
    monkeypatch.setattr(_run, 'host_report', _doctor.host_report)
    monkeypatch.setenv('PATH', str(empty))
    harness, _, _ = _inline(tmp_path)
    code, out, _ = _cli_run(capsys, target, tmp_path / 'run', *harness)
    stop = cast(dict[str, object], json.loads(out))
    assert code == 1 and 'draft' in stop and 'doctor' not in stop


# ---------------------------------------------------------------- AC-13 changed harness on resume

def _failed_first_run(tmp_path: Path, target: Path, write_draft: Callable[..., list[str]],
                      capsys: pytest.CaptureFixture[str], harness: list[str]) -> Path:
    out = tmp_path / 'run'
    _ = write_draft(out)
    code, _, _ = _Driver(capsys, target, out, harness).finish()
    assert code != 0 and (out / 'run.json').is_file()
    return out


def test_configResume_afterTheScriptContentChangesAtTheSamePath_stopsWithHarnessChanged(
        tmp_path: Path, target: Path, write_draft: Callable[..., list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    config, wrapper, log = _config(tmp_path, 'fail')
    harness = ['--harness-config', str(config)]
    out = _failed_first_run(tmp_path, target, write_draft, capsys, harness)
    before = log.read_text()
    with wrapper.open('a') as stream:
        _ = stream.write('\n# changed\n')
    code, _, err = _Driver(capsys, target, out, harness).call()
    assert code != 0 and err['code'] == 'harness-changed' and log.read_text() == before


@pytest.mark.parametrize('field', ['model', 'identity'])
def test_configResume_afterARoleFieldChanges_stopsWithHarnessChanged(
        tmp_path: Path, target: Path, write_draft: Callable[..., list[str]], capsys: pytest.CaptureFixture[str],
        field: str) -> None:
    config, _, log = _config(tmp_path, 'fail')
    harness = ['--harness-config', str(config)]
    out = _failed_first_run(tmp_path, target, write_draft, capsys, harness)
    before = log.read_text()
    document = read(config)
    document[field] = 'changed'
    _ = config.write_text(json.dumps(document))
    code, _, err = _Driver(capsys, target, out, harness).call()
    assert code != 0 and err['code'] == 'harness-changed' and log.read_text() == before


def test_configResume_withTheConfigDroppedOnTheCli_stopsWithHarnessChangedAndNoCall(
        tmp_path: Path, target: Path, write_draft: Callable[..., list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    config, _, log = _config(tmp_path, 'fail')
    out = _failed_first_run(tmp_path, target, write_draft, capsys, ['--harness-config', str(config)])
    before = log.read_text()
    code, _, err = _Driver(capsys, target, out, []).call()
    assert code != 0 and err['code'] == 'harness-changed' and log.read_text() == before


def test_configResume_withOnlyHarnessCodex_stopsWithACodedErrorAndNoCall(
        tmp_path: Path, target: Path, write_draft: Callable[..., list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    config, _, log = _config(tmp_path, 'fail')
    out = _failed_first_run(tmp_path, target, write_draft, capsys, ['--harness-config', str(config)])
    before = log.read_text()
    code, _, err = _Driver(capsys, target, out, ['--harness', 'codex']).call()
    assert code != 0 and 'code' in err and log.read_text() == before


def test_inlineResume_withADifferentExecutableName_stopsWithHarnessChanged(
        tmp_path: Path, target: Path, write_draft: Callable[..., list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    harness, _, log = _inline(tmp_path, 'fail')
    out = _failed_first_run(tmp_path, target, write_draft, capsys, harness)
    before = log.read_text()
    other = tmp_path / 'other-python'
    other.symlink_to(sys.executable)
    swapped = [harness[0], harness[1], f'--command={other}', *harness[3:]]
    code, _, err = _Driver(capsys, target, out, swapped).call()
    assert code != 0 and err['code'] == 'harness-changed' and log.read_text() == before


# ---------------------------------------------------------------- AC-14 / AC-15 Gemini wrapper

GEMINI_SCHEMA: dict[str, object] = {'type': 'object', 'properties': {'x': {'type': 'string'}}, 'required': ['x']}


def _request(operation: object, root: Path, **payload: object) -> str:
    return json.dumps({'schema_version': 1, 'operation': operation, 'workspace': str(root), 'model': 'm', **payload})


def _bin(tmp_path: Path, name: str = 'bin') -> Path:
    directory = tmp_path / name
    directory.mkdir()
    for tool, source in {'gemini': 'fake_gemini.py', 'docker': 'fake_docker.py'}.items():
        script = directory / tool
        _ = script.write_text(f'#!{sys.executable}\n' + (FIXTURES / source).read_text())
        script.chmod(0o700)
    return directory


def _logged(path: Path) -> list[object]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    '''A runner-shaped workspace: `<temporary>/skillz-x/workspace` that already holds `.agents`.'''
    path = tmp_path / 'skillz-x/workspace'
    (path / '.agents').mkdir(parents=True)
    return path


@pytest.mark.parametrize(('raw', 'message'), [
    *[(raw, 'request is not JSON') for raw in ['', '   ', '{', 'nul']],
    *[(raw, 'request must be a JSON object') for raw in ['null', '1', '"x"', '[]', '[1]', 'true']],
    ('{"schema_version": 1}', 'unknown operation'),
    ('{"schema_version": 1, "operation": "sandbox"}', 'workspace must be an existing absolute directory'),
])
def test_respond_nonRequests_raiseValueErrorOnly(tmp_path: Path, raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _ = _gemini.respond(raw, bin_dir=_bin(tmp_path))


@pytest.mark.parametrize('version', [2, 0, '1', 1.0, True, None, [1], {'v': 1}, -1])
def test_respond_wrongProtocolVersion_isRejectedBeforeAnyTool(tmp_path: Path, workspace: Path, version: object) -> None:
    directory = _bin(tmp_path)
    raw = json.dumps({'schema_version': version, 'operation': 'sandbox', 'workspace': str(workspace), 'argv': ['true']})
    with pytest.raises(ValueError, match='unsupported schema_version'):
        _ = _gemini.respond(raw, bin_dir=directory)
    assert _logged(directory / 'docker.log') == []


@pytest.mark.parametrize('operation', ['', 'SANDBOX', 'sandbox ', 'exec', None, 1, True, ['sandbox'], {'a': 1}, [], {}])
def test_respond_unknownOperation_raisesValueErrorNotTypeError(tmp_path: Path, workspace: Path, operation: object) -> None:
    '''AC-14: an unhashable operation (list, dict) is a bad request. The set membership test raises TypeError instead.'''
    directory = _bin(tmp_path)
    with pytest.raises(ValueError, match='unknown operation'):
        _ = _gemini.respond(_request(operation, workspace, argv=['true']), bin_dir=directory)
    assert _logged(directory / 'docker.log') == []


@pytest.mark.parametrize('workspace_value', [None, 5, '', 'relative/dir', '/definitely/not/a/dir', '/tmp/' + chr(0) + 'x', ['/tmp']])
def test_respond_badWorkspace_raisesValueError(tmp_path: Path, workspace_value: object) -> None:
    raw = json.dumps({'schema_version': 1, 'operation': 'sandbox', 'workspace': workspace_value, 'argv': ['true']})
    with pytest.raises(ValueError, match='workspace must be|embedded null character'):
        _ = _gemini.respond(raw, bin_dir=_bin(tmp_path))


@pytest.mark.parametrize('argv', [None, 'true', [], [''], [1], ['true', 2], ['true', ''], [None], [['true']], {'0': 'true'}, 0])
def test_sandbox_badArgv_raisesValueErrorBeforeAnyRuntimeCall(tmp_path: Path, workspace: Path, argv: object) -> None:
    directory = _bin(tmp_path)
    with pytest.raises(ValueError, match='argv'):
        _ = _gemini.respond(_request('sandbox', workspace, argv=argv), bin_dir=directory)
    assert _logged(directory / 'docker.log') == []


@pytest.mark.parametrize('first', ['--privileged', '-v', '--network=host', '--volume=/:/host', '--entrypoint=sh', '-i', '--rm'])
def test_sandbox_flagLikeArgv_staysAfterTheImageAndNeverBecomesADockerFlag(
        tmp_path: Path, workspace: Path, first: str) -> None:
    directory = _bin(tmp_path)
    _ = _gemini.respond(_request('sandbox', workspace, argv=[first, 'second']), bin_dir=directory)
    flags = cast(list[str], _logged(directory / 'docker.log')[0])
    image = flags.index(_gemini.IMAGE)
    seconds = str(_gemini.TIMEOUTS['sandbox'] + _gemini.CONTAINER_GRACE)
    assert flags[image + 1:image + 5] == ['-s', 'KILL', seconds, '/usr/bin/env']
    assert flags[image + 5 + len(_gemini.CONTAINER_ENV):] == [first, 'second']
    assert flags[:image].count('--entrypoint') == 1 and flags[flags.index('--entrypoint') + 1] == '/usr/bin/timeout'
    assert flags[:image].count(first) == (1 if first == '--rm' else 0)
    assert '--privileged' not in flags[:image] and '--network=host' not in flags[:image]
    assert flags[flags.index('--network') + 1] == 'none' and flags[flags.index('--cap-drop') + 1] == 'ALL'
    assert flags[flags.index('--security-opt') + 1] == 'no-new-privileges'
    assert flags[flags.index('--name') + 1].startswith('skillz-')
    mounts = [flags[index + 1] for index, flag in enumerate(flags[:image]) if flag == '--mount']
    assert len(mounts) == 2 and all(mount.startswith('type=bind,source=') for mount in mounts)
    assert '-v' not in flags[:image] and not any(flag.startswith('--volume') for flag in flags[:image])


def test_sandbox_enormousStdout_isReturnedWithoutCrashing(tmp_path: Path, workspace: Path) -> None:
    directory = _bin(tmp_path)
    docker = directory / 'docker'
    _ = docker.write_text(f"#!{sys.executable}\nimport sys\nsys.stdout.write('x' * 5_000_000)\nsys.stderr.write('e' * 2_000_000)\n")
    result = _gemini.respond(_request('sandbox', workspace, argv=['true']), bin_dir=directory)
    assert result['returncode'] == 0 and len(cast(str, result['stdout'])) == 5_000_000


def test_sandbox_nonUtf8Output_doesNotCrashTheWrapper(tmp_path: Path, workspace: Path) -> None:
    '''AC-14: undecodable bytes from the sandboxed command must not kill the wrapper (text=True decodes strictly).'''
    directory = _bin(tmp_path)
    docker = directory / 'docker'
    _ = docker.write_text(f'#!{sys.executable}\nimport sys\nsys.stdout.buffer.write(bytes([255, 254]) + b" bad")\n')
    result = _gemini.respond(_request('sandbox', workspace, argv=['true']), bin_dir=directory)
    assert result['returncode'] == 0 and isinstance(result['stdout'], str)


def _gemini_script(directory: Path, depth: int) -> None:
    script = directory / 'gemini'
    nested = "'{\"a\":' * " + str(depth) + " + '1' + '}' * " + str(depth)
    event = "{'type': 'message', 'role': 'assistant', 'content': " + nested + '}'
    _ = script.write_text(f'#!{sys.executable}\nimport sys, json\nsys.stdout.write(json.dumps({event}))\n')
    script.chmod(0o700)


@pytest.mark.parametrize('depth', [50, 400, 900, 990, 2_000, 100_000])
def test_infer_deeplyNestedModelAnswer_isACleanResultOrACleanError(tmp_path: Path, workspace: Path, depth: int) -> None:
    '''AC-14: any nesting depth yields a serialisable dict or a RuntimeError, never a RecursionError.'''
    directory = _bin(tmp_path)
    _gemini_script(directory, depth)
    try:
        result = _gemini.respond(_request('infer', workspace, prompt='p', response_schema=GEMINI_SCHEMA), bin_dir=directory)
    except RecursionError:
        pytest.fail('the nesting depth bound is gone: respond raised RecursionError')
    except (ValueError, RuntimeError) as error:
        assert 'recursion' not in str(error).lower()
        return
    _ = json.dumps(result)


@pytest.mark.parametrize('depth', [900, 990, 2_000, 100_000])
def test_wrapperCli_deeplyNestedModelAnswer_neverEndsInATraceback(tmp_path: Path, workspace: Path, depth: int) -> None:
    directory = _bin(tmp_path)
    _gemini_script(directory, depth)
    done = subprocess.run([sys.executable, '-c', WRAPPER_MAIN, 'harness-wrapper', 'gemini', '--bin-dir', str(directory)],
                          input=_request('infer', workspace, prompt='p', response_schema=GEMINI_SCHEMA),
                          capture_output=True, text=True, check=False)
    assert 'Traceback' not in done.stderr and 'RecursionError' not in done.stderr
    assert 'recursion' not in done.stderr.lower()
    assert done.returncode in (0, 1)


@pytest.mark.parametrize('depth', [1_000, 100_000])
def test_wrapperCli_deeplyNestedRequest_neverEndsInATraceback(depth: int) -> None:
    '''AC-14: a request nested past the recursion limit is a bad request, reported as a JSON error.'''
    done = subprocess.run([sys.executable, '-c', WRAPPER_MAIN, 'harness-wrapper', 'gemini'],
                          input='[' * depth + ']' * depth, capture_output=True, text=True, check=False)
    assert done.returncode == 1 and done.stdout == ''
    assert 'Traceback' not in done.stderr and 'error' in json.loads(done.stderr)


def test_wrapperCli_unhashableOperation_isAJsonError() -> None:
    raw = '{"schema_version": 1, "operation": ["x"], "workspace": "/tmp"}'
    done = subprocess.run([sys.executable, '-c', WRAPPER_MAIN, 'harness-wrapper', 'gemini'], input=raw,
                          capture_output=True, text=True, check=False)
    assert done.returncode == 1 and 'Traceback' not in done.stderr and 'error' in json.loads(done.stderr)


def _event_script(directory: Path, *events: object, code: int = 0) -> None:
    script = directory / 'gemini'
    lines = '\n'.join(json.dumps(event) for event in events)
    _ = script.write_text(f'#!{sys.executable}\nimport sys\nsys.stdout.write({lines!r})\nsys.exit({code})\n')
    script.chmod(0o700)


def _said(content: object) -> dict[str, object]:
    return {'type': 'message', 'role': 'assistant', 'content': content}


@pytest.mark.parametrize(('events', 'code', 'message'), [
    ([], 9, 'gemini exits 9'),
    ([], 0, 'no assistant answer'),
    ([{'type': 'result', 'status': 'error', 'error': {'message': 'boom'}}], 0, 'reports an error'),
    ([_said(5)], 0, 'no assistant answer'),
    ([_said('[1]')], 0, 'not a JSON object'),
    ([_said('')], 0, 'no assistant answer'),
])
def test_infer_geminiFaults_areCleanRuntimeErrors(
        tmp_path: Path, workspace: Path, events: list[object], code: int, message: str) -> None:
    directory = _bin(tmp_path)
    _event_script(directory, *events, code=code)
    with pytest.raises(RuntimeError, match=message):
        _ = _gemini.respond(_request('infer', workspace, prompt='p', response_schema=GEMINI_SCHEMA), bin_dir=directory)


def test_infer_plainTextOutput_isACleanRuntimeError(tmp_path: Path, workspace: Path) -> None:
    directory = _bin(tmp_path)
    script = directory / 'gemini'
    _ = script.write_text(f"#!{sys.executable}\nprint('hello')\n")
    script.chmod(0o700)
    with pytest.raises(RuntimeError, match='no assistant answer'):
        _ = _gemini.respond(_request('infer', workspace, prompt='p', response_schema=GEMINI_SCHEMA), bin_dir=directory)


@pytest.mark.parametrize('stats', [{'models': [1, 2]}, {'models': {'m': {'tokens': {'prompt': -5, 'cached': True, 'candidates': 1.5}}}}, 7])
def test_infer_garbageStats_recordNoUsage(tmp_path: Path, workspace: Path, stats: object) -> None:
    directory = _bin(tmp_path)
    _event_script(directory, _said(json.dumps({'x': 'y'})), {'type': 'result', 'status': 'success', 'stats': stats})
    result = _gemini.respond(_request('infer', workspace, prompt='p', response_schema=GEMINI_SCHEMA), bin_dir=directory)
    assert result['usage'] is None and result['answer'] == {'x': 'y'}


@pytest.mark.parametrize(('requested', 'missing'), [('podman', ['podman']), ('runsc', ['runsc']), (None, ['docker', 'podman'])])
def test_missingRuntime_namesTheRuntimeAndItsFix(
        tmp_path: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch, requested: str | None, missing: list[str]) -> None:
    empty = tmp_path / 'empty'
    empty.mkdir()
    monkeypatch.setenv('PATH', str(empty))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(_gemini, '_extra_dirs', lambda: list[Path]())
    monkeypatch.setattr(sys, 'platform', 'linux')
    with pytest.raises(RuntimeError) as caught:
        _ = _gemini.respond(_request('sandbox', workspace, argv=['true']), runtime=requested)
    fixes = '; or '.join(f'{kind}: {_gemini.INSTALL[kind]}' for kind in missing)
    assert str(caught.value) == (f'no sandbox runtime for Gemini CLI found (missing {", ".join(missing)}). Fix: {fixes}. '
                                 + 'The wrapper does not drive LXC.')


def test_unknownRuntimeName_isAUsageError(tmp_path: Path, workspace: Path) -> None:
    '''--runtime is a Literal: fromargs refuses `lxc` as a usage error before any request is read or container started.'''
    directory = _bin(tmp_path)
    done = subprocess.run([sys.executable, '-c', WRAPPER_MAIN, 'harness-wrapper', 'gemini', '--bin-dir', str(directory),
                           '--runtime', 'lxc'], input=_request('sandbox', workspace, argv=['true']),
                          capture_output=True, text=True, check=False)
    assert done.returncode != 0 and done.stdout == '' and 'Traceback' not in done.stderr
    assert 'runtime' in done.stderr and 'lxc' in done.stderr
    assert _logged(directory / 'docker.log') == []


def test_binDir_beatsAnEarlierEntryOnPathAndTheExtraDirectories(
        tmp_path: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    preferred = _bin(tmp_path, 'preferred')
    decoy = _bin(tmp_path, 'decoy')
    extra = _bin(tmp_path, 'extra')
    monkeypatch.setenv('PATH', str(decoy))
    monkeypatch.setattr(_gemini, '_extra_dirs', lambda: [extra])
    monkeypatch.setattr(sys, 'platform', 'linux')
    _ = _gemini.respond(_request('sandbox', workspace, argv=['true']), bin_dir=preferred)
    assert _logged(preferred / 'docker.log') and not _logged(decoy / 'docker.log') and not _logged(extra / 'docker.log')


def test_discover_skillNameWithRegexMetacharacters_isRefusedBeforeGeminiRuns(tmp_path: Path, workspace: Path) -> None:
    '''A regex-looking name cannot select other skills: it fails the name check, so gemini never starts.'''
    directory = _bin(tmp_path)
    (workspace / '.agents/skills/abc').mkdir(parents=True)
    _ = (workspace / '.agents/skills/abc/SKILL.md').write_text('x')
    for name in ['a.c', 'a|abc', '.*', '(', 'abc)']:
        raw = _request('discover', workspace, skill_name=name, skill_path=str(workspace / '.agents/skills' / name))
        with pytest.raises(ValueError, match='skill_name and skill_path'):
            _ = _gemini.respond(raw, bin_dir=directory)
    assert _logged(directory / 'gemini.log') == []


def test_discover_prefixOfAnInstalledSkill_doesNotMatchIt(tmp_path: Path, workspace: Path) -> None:
    directory = _bin(tmp_path)
    (workspace / '.agents/skills/abc').mkdir(parents=True)
    _ = (workspace / '.agents/skills/abc/SKILL.md').write_text('x')
    found = _gemini.respond(_request('discover', workspace, skill_name='ab', skill_path=str(workspace / '.agents/skills/ab')),
                            bin_dir=directory)
    assert found['skills'] == [{'name': 'abc', 'path': str(workspace / '.agents/skills/abc')}]


def test_discover_skillPathOutsideTheWorkspace_isRefusedBeforeGeminiRuns(tmp_path: Path, workspace: Path) -> None:
    directory = _bin(tmp_path)
    for path in ['/p', str(workspace / '.agents/skills/other'), str(workspace / '.agents/skills/abc/../abc')]:
        with pytest.raises(ValueError, match='skill_path must be'):
            _ = _gemini.respond(_request('discover', workspace, skill_name='abc', skill_path=path), bin_dir=directory)
    assert _logged(directory / 'gemini.log') == []


# ---------------------------------------------------------------- AC-7 credential kind

@pytest.fixture
def codex_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if Path('/etc/codex/skills').exists():
        pytest.skip('admin skill roots stop the Codex transport')
    stub = tmp_path / 'codexbin/codex'
    stub.parent.mkdir()
    _ = stub.write_text('#!/bin/sh\nexit 0\n')
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv('PATH', f'{stub.parent}:{os.environ["PATH"]}')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    host = tmp_path / 'host'
    host.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(host))
    return host


def _credential() -> str:
    codex = Codex('m', Budget(1, 120, 0), lambda: None)
    try:
        return codex.credential_kind
    finally:
        codex.close()


@pytest.mark.parametrize('shape', ['file', 'symlink-to-file', 'dangling-symlink', 'directory', 'absent', 'symlink-to-directory'])
def test_codexCredential_followsAUsableAuthFileOnlyAndNeedsNoApiKey(codex_on_path: Path, tmp_path: Path, shape: str) -> None:
    auth = codex_on_path / 'auth.json'
    if shape == 'file':
        _ = auth.write_text('{"tokens": {"id_token": "t"}}')
    elif shape == 'symlink-to-file':
        real = tmp_path / 'real-auth.json'
        _ = real.write_text('{"tokens": {"id_token": "t"}}')
        auth.symlink_to(real)
    elif shape == 'dangling-symlink':
        auth.symlink_to(tmp_path / 'gone.json')
    elif shape == 'directory':
        auth.mkdir()
    elif shape == 'symlink-to-directory':
        auth.symlink_to(tmp_path)
    expected = SUBSCRIPTION_LOGIN if shape in {'file', 'symlink-to-file'} else MISSING_CREDENTIAL
    assert _credential() == expected


@pytest.mark.parametrize('shape', ['dangling-symlink', 'directory'])
def test_codexDoctor_withAnUnusableAuthFile_failsTheCredentialRowWithAFix(
        codex_on_path: Path, tmp_path: Path, shape: str) -> None:
    auth = codex_on_path / 'auth.json'
    if shape == 'directory':
        auth.mkdir()
    else:
        auth.symlink_to(tmp_path / 'gone.json')
    rows = {cast(str, row['check']): row for row in cast(list[dict[str, object]], _doctor.doctor('codex')['checks'])}
    assert rows['credential']['status'] == 'fail' and rows['credential']['detail'] == MISSING_CREDENTIAL
    assert rows['credential']['fix']


def test_codexDoctor_withALoginAndNoApiKey_neverFailsTheCredentialRow(codex_on_path: Path) -> None:
    _ = (codex_on_path / 'auth.json').write_text('{"tokens": {"id_token": "t"}}')
    rows = {cast(str, row['check']): row for row in cast(list[dict[str, object]], _doctor.doctor('codex')['checks'])}
    assert rows['credential']['status'] == 'pass' and rows['credential']['detail'] == SUBSCRIPTION_LOGIN


# ---------------------------------------------------------------- AC-5 isolation option gone

@pytest.mark.parametrize('argv', [
    ['run', '--target', 't', '--out', 'o', '--model', 'm', '--edit', 'prose', '--isolation', 'claude'],
    ['run', '--target', 't', '--out', 'o', '--model', 'm', '--edit', 'prose', '--isolation=nono'],
    ['doctor', '--isolation', 'nono'],
    ['doctor', '--isolation=claude'],
])
def test_isolationOption_isRefusedByRunAndDoctor(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(argv) != 0
    assert not (tmp_path / 'o').exists()
    assert 'isolation' in capsys.readouterr().err


@pytest.mark.parametrize('command', ['run', 'doctor', 'self-test'])
def test_help_mentionsNoNonoOrIsolationOption(capsys: pytest.CaptureFixture[str], command: str) -> None:
    try:
        _ = main([command, '--help'])
    except SystemExit:
        pass
    text = capsys.readouterr()
    shown = (text.out + text.err).lower()
    assert shown and 'nono' not in shown and '--isolation' not in shown


def test_bundle_help_listsHarnessConfigAndCommandButNoIsolation() -> None:
    bundle = REPO / 'skills/skillz/scripts/skillz-experiment.pyz'
    done = subprocess.run([sys.executable, str(bundle), 'run', '--help'], capture_output=True, text=True, check=False)
    shown = (done.stdout + done.stderr).lower()
    assert '--harness-config' in shown and '--command' in shown and '--isolation' not in shown and 'nono' not in shown


# ---------------------------------------------------------------- review fixes

def _plain(target: Path, out: Path, *extra: str) -> list[str]:
    return ['run', '--target', str(target), '--out', str(out), '--model', 'offline', '--edit', 'prose', *extra]


def test_noLiveConfigRun_stopsAtLiveRequired_andAResumeWithoutTheConfigIsHarnessChanged(
        tmp_path: Path, target: Path, write_draft: Callable[..., list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    config, _, log = _config(tmp_path)
    out = tmp_path / 'run'
    _ = write_draft(out)
    flags = ['--harness-config', str(config)]
    assert main(_plain(target, out, *flags)) == 1
    case_hash = cast(str, json.loads(capsys.readouterr().out)['case_hash'])
    assert main(_plain(target, out, *flags, '--approve-cases', case_hash)) == 1
    calls = str(cast(dict[str, int], json.loads(capsys.readouterr().out)['estimate'])['calls'])
    assert main(_plain(target, out, *flags, '--approve-cases', case_hash, '--approve-budget', calls)) == 1
    assert json.loads(capsys.readouterr().err)['code'] == 'live-required'
    record = read(out / 'run.json')
    assert record['adapter'] == 'config' and 'harness' in record
    assert main(_plain(target, out, '--live')) != 0
    assert json.loads(capsys.readouterr().err)['code'] == 'harness-changed'
    assert _ops(log) == []


def _failing_self_test(tmp_path: Path, capsys: pytest.CaptureFixture[str], stderr_script: str) -> tuple[int, str]:
    script = tmp_path / 'failing.py'
    _ = script.write_text(stderr_script)
    config = tmp_path / 'harness.json'
    _ = config.write_text(json.dumps({'schema_version': 1, 'adapter': 'command', 'identity': 'failing',
                                      'command': [sys.executable, str(script)]}))
    code = main(['self-test', '--preflight-only', '--model', 'm', '--harness-config', str(config)])
    return code, str(cast(dict[str, object], json.loads(capsys.readouterr().err))['error'])


def test_selfTest_throughACommandWhoseWrapperFails_keepsRawStderrInAPrivateFile(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, error = _failing_self_test(tmp_path, capsys, 'import sys\nprint("first noise", file=sys.stderr)\n'
                                     + 'print("quota key=SECRET-1 exhausted", file=sys.stderr)\nsys.exit(4)\n')
    assert code != 0 and 'harness command fails' in error
    assert 'SECRET-1' not in error and 'first noise' not in error
    kept = Path(error.rsplit(': ', 1)[1])
    try:
        assert 'SECRET-1' in kept.read_text() and kept.stat().st_mode & 0o077 == 0
    finally:
        kept.unlink()


def test_selfTest_throughACommandWhoseWrapperFails_showsOnlyTheWrapperMarkedErrorLine(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, error = _failing_self_test(
        tmp_path, capsys, 'import sys,json\nprint("noise", file=sys.stderr)\n'
        + 'print(json.dumps({"error": "install docker", "exit_code": 1}), file=sys.stderr)\n'
        + 'print("https://x/?key=SECRET-2", file=sys.stderr)\nsys.exit(1)\n')
    assert code != 0 and 'harness command fails; no unsafe fallback: install docker; private stderr tail: ' in error
    assert 'SECRET-2' not in error
    Path(error.rsplit(': ', 1)[1]).unlink()


def test_optionEmbeddedPath_insideTheTarget_isRefused(tmp_path: Path, target: Path) -> None:
    configuration = Configuration.command([sys.executable, '--bin-dir=' + str(target / 'bin')], 'm')
    with pytest.raises(ValueError, match='outside the candidate'):
        configuration.check_boundary(target)
    configuration.check_boundary(tmp_path / 'elsewhere')


def test_optionEmbeddedFile_isHashedIntoTheFingerprint(tmp_path: Path) -> None:
    settings = tmp_path / 'settings.json'
    _ = settings.write_text('{"a": 1}')
    argv = [sys.executable, '--opt=' + str(settings)]
    before = Configuration.command(argv, 'm').roles['task'].fingerprint()
    _ = settings.write_text('{"a": 2}')
    assert Configuration.command(argv, 'm').roles['task'].fingerprint() != before


@pytest.mark.parametrize('form', ['PYTHONPATH={}', '-r{}'])
def test_envAssignmentAndAttachedShortOption_insideTheTarget_areRefused(tmp_path: Path, target: Path, form: str) -> None:
    configuration = Configuration.command([sys.executable, form.format(target / 'lib')], 'm')
    with pytest.raises(ValueError, match='outside the candidate'):
        configuration.check_boundary(target)
    configuration.check_boundary(tmp_path / 'elsewhere')


@pytest.mark.parametrize('form', ['PYTHONPATH={}', '-r{}'])
def test_envAssignmentAndAttachedShortOption_file_isHashedIntoTheFingerprint(tmp_path: Path, form: str) -> None:
    hook = tmp_path / 'hook.js'
    _ = hook.write_text('one')
    argv = [sys.executable, form.format(hook)]
    before = Configuration.command(argv, 'm').roles['task'].fingerprint()
    _ = hook.write_text('two')
    assert Configuration.command(argv, 'm').roles['task'].fingerprint() != before


@pytest.mark.parametrize(('value', 'code'), [('nono', 'nono-removed'), ('claude', 'isolation-removed')])
def test_isolationKey_inAnyRole_isRefusedWithItsOwnCode(
        tmp_path: Path, target: Path, capsys: pytest.CaptureFixture[str], value: str, code: str) -> None:
    config = tmp_path / 'cfg.json'
    _ = config.write_text(json.dumps({'schema_version': 1, 'roles': {'task': {'adapter': 'codex', 'isolation': value}}}))
    assert main(_plain(target, tmp_path / 'run', '--live', '--harness-config', str(config))) != 0
    assert json.loads(capsys.readouterr().err)['code'] == code


@pytest.mark.parametrize(('flags', 'message'), [
    (['--harness', 'claude', '--harness-config', 'X'], '--harness-config cannot combine with --harness or --command'),
    (['--command=python3', '--harness-config', 'X'], '--harness-config cannot combine with --harness or --command'),
    (['--command=python3'], '--command requires --harness command'),
    (['--harness', 'command'], '--harness command requires --command ARGV...'),
])
def test_harnessOptionConflicts_areUsageErrors(
        tmp_path: Path, target: Path, capsys: pytest.CaptureFixture[str], flags: list[str], message: str) -> None:
    config, _, _ = _config(tmp_path)
    flags = [str(config) if flag == 'X' else flag for flag in flags]
    assert main(_plain(target, tmp_path / 'run', '--live', *flags)) != 0
    err = cast(dict[str, object], json.loads(capsys.readouterr().err))
    assert err['code'] == 'usage' and err['error'] == message
