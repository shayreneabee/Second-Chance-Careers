from .adzuna import AdzunaProvider
from .careeronestop import CareerOneStopProvider
from .remoteok import RemoteOKProvider
from .usajobs import USAJobsProvider


def default_providers():
    return [
        CareerOneStopProvider(),
        USAJobsProvider(),
        AdzunaProvider(),
        RemoteOKProvider(),
    ]

