"""First-deployment absence admission using the existing authenticated GET reader.

Opt-in trusted integration code only. No existing-object ownership policy,
approval/platform/token authenticator, CLI default, retry or write is added.
An exact-object 404 is admission of absence, never a grant to execute.
"""
from delivery_object_reader import ObjectReader, Observation, safe_code
from delivery_provider import AdmittedBaseline
from delivery_verifier import (Progress, ReadRequest, VerificationError,
                               provenance_checked, require)
import delivery_adapter as core


ERRORS = frozenset('''BASELINE_CONFIGURATION_INVALID BASELINE_REQUEST_INVALID
BASELINE_INITIAL_PROGRESS_REQUIRED BASELINE_PROGRESS_CHANGED
BASELINE_OBJECT_PRESENT BASELINE_OBSERVATION_INVALID'''.split())


class FirstDeploymentBaseline:
    """Admit exact-profile fresh, classified 404s before this run's first write.

    Reviewed site code supplies its pinned ObjectReader (same credential,
    binding, CA/endpoint) after authenticating the platform and token. Reader
    TLS authentication alone does not authenticate issuer/audience/RBAC/life.
    provenance records that trusted integration's authority reference; its
    strings and this class are not a new approval or authenticity validator.

    Reads share request.deadline and recheck durable progress before/after.
    They are sequential, not an atomic snapshot; the existing claim, exclusive
    window and subsequent ObjectProvider re-read remain necessary.
    """
    def __init__(self, reader, *, progress, provenance):
        require(type(reader) is ObjectReader and callable(progress),
                'BASELINE_CONFIGURATION_INVALID')
        provenance_checked(provenance)
        self.reader, self.progress, self.provenance = reader, progress, provenance

    def __repr__(self):
        return '<P2 FirstDeploymentBaseline: context redacted>'

    def check(self, request):
        require(type(request) is ReadRequest and type(request.progress) is Progress
                and request.binding.action == 'deploy', 'BASELINE_REQUEST_INVALID')
        self.reader.check_request(request)  # exact binding/credential and deadline
        require(request.progress == Progress(), 'BASELINE_INITIAL_PROGRESS_REQUIRED')
        current = self.progress()
        require(type(current) is Progress and current == request.progress,
                'BASELINE_PROGRESS_CHANGED')
        # A progress callback must not consume the deadline unnoticed.
        self.reader.check_request(request)

    def __call__(self, request):
        try:
            self.check(request)
            for key in core.objects_for(request.binding):
                self.check(request)
                observation = self.reader.read(request, key)
                self.check(request)
                require(type(observation) is Observation and observation.key == key
                        and type(observation.status) is int
                        and observation.status in (200, 404),
                        'BASELINE_OBSERVATION_INVALID')
                require(observation.status == 404, 'BASELINE_OBJECT_PRESENT')
                # Exact Status/Failure/NotFound resource/name/group validation
                # is performed by ObjectReader before it returns Observation.
            self.check(request)
            provenance_checked(self.provenance)
            return AdmittedBaseline(request, request.binding, self.provenance,
                                    (None,) * len(core.objects_for(request.binding)))
        except Exception as error:
            code = (error.args[0] if type(error) is VerificationError
                    and len(error.args) == 1 and type(error.args[0]) is str
                    and error.args[0] in ERRORS else safe_code(error, 'BASELINE_ADMISSION_FAILED'))
            raise VerificationError(code) from None
