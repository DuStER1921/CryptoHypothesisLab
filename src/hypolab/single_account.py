"""One-Gmail role policy: separate OAuth *tokens*, NOT separate account ACLs.

This is a local preflight, not proof of effective Google token scopes or an
operating-system sandbox. No credentials are created, stored or refreshed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


class OneAccountError(PermissionError):
    pass


READ_SCOPE = 'https://www.googleapis.com/auth/drive.readonly'
WRITE_SCOPE = 'https://www.googleapis.com/auth/drive.file'
# A reader may see OOS at Google-account level. It must never be given to an
# analyst process, or used to select files outside a signed Development allowlist.
BLOCKED_ANALYST_ENV = frozenset({
    'GOOGLE_APPLICATION_CREDENTIALS', 'GOOGLE_OAUTH_ACCESS_TOKEN',
    'GOOGLE_DRIVE_TOKEN', 'GOOGLE_DRIVE_REFRESH_TOKEN', 'GDRIVE_TOKEN',
    'DRIVE_TOKEN', 'CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE',
})


@dataclass(frozen=True)
class TokenScopeDeclaration:
    """Requested scopes from separately stored tokens. NOT introspection proof."""
    source_requested: tuple[str, ...]
    uploader_requested: tuple[str, ...]
    source_token_file: Path
    uploader_token_file: Path

    def check_local(self) -> None:
        if set(self.source_requested) != {READ_SCOPE} or len(self.source_requested) != 1:
            raise OneAccountError('source token must request drive.readonly only')
        if set(self.uploader_requested) != {WRITE_SCOPE} or len(self.uploader_requested) != 1:
            raise OneAccountError('output token must request drive.file only')
        if Path(self.source_token_file).is_symlink() or Path(self.uploader_token_file).is_symlink():
            raise OneAccountError('symlink credential path forbidden')
        source = Path(self.source_token_file).resolve(strict=False)
        output = Path(self.uploader_token_file).resolve(strict=False)
        if (source == output or source in output.parents or output in source.parents
                or source.parent == output.parent):
            raise OneAccountError('reader/writer tokens require separate private directories')


def preflight_analyst_environment(*, allowed_root: Path, forbidden_roots: tuple[Path, ...],
                                 environ: dict[str, str] | None = None) -> None:
    """Checks a credential-free analyst process at launch; no OS isolation claim.

    For a real analyst use a separate unprivileged OS user/container without
    source mount, owner SQLite, OAuth cache or socket to the source broker.
    """
    env = dict(os.environ if environ is None else environ)
    if any(env.get(x) for x in BLOCKED_ANALYST_ENV):
        raise OneAccountError('Google credential-bearing environment detected')
    # A Colab runtime marker alone is not a credential. Its mounted Drive is.
    colab_drive=Path('/content/drive')
    if colab_drive.is_mount() or (colab_drive / 'MyDrive').exists():
        raise OneAccountError('Google Drive mounted in analyst runtime')
    if Path(allowed_root).is_symlink():
        raise OneAccountError('analyst workspace symlink denied')
    root = Path(allowed_root).resolve(strict=True)
    if not root.is_dir():
        raise OneAccountError('missing or aliased analyst root')
    if not forbidden_roots:
        raise OneAccountError('explicit forbidden source/cache roots required')
    # HOME/user profile and config caches must not share owner credentials.
    for key in ('HOME','USERPROFILE','XDG_CONFIG_HOME','APPDATA'):
        if env.get(key):
            candidate = Path(env[key]).resolve(strict=False)
            if candidate != root and root not in candidate.parents:
                raise OneAccountError('analyst profile/cache outside isolated workspace')
    for item in forbidden_roots:
        original = Path(item)
        forbidden = original.resolve(strict=True)
        if original.is_symlink() or not forbidden.is_dir():
            raise OneAccountError('source path check invalid')
        if root == forbidden or root in forbidden.parents or forbidden in root.parents:
            raise OneAccountError('analyst root overlaps sensitive data')
