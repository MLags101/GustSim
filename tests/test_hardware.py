"""Worker hardware report: physical cores, container quota and the recommended MPI count."""
from gustsim import hardware

CPUINFO = '\n\n'.join(f'processor\t: {i}\nphysical id\t: 0\ncore id\t\t: {i % 10}\ncpu cores\t: 10\nsiblings\t: 20' for i in range(20))


def test_physical_cores_count_distinct_cores_not_threads():
    assert hardware.physical_cores(CPUINFO) == 10


def test_cgroup_quota():
    assert hardware.cpu_quota('600000 100000\n') == 6.0
    assert hardware.cpu_quota('max 100000\n') is None
    assert hardware.cpu_quota('') is None


def test_recommendation_uses_physical_cores_within_the_quota_and_leaves_the_post_lane_a_core():
    assert hardware.recommended_processes(10, 20, None, 1) == 9
    assert hardware.recommended_processes(10, 20, 6.0, 1) == 5
    assert hardware.recommended_processes(2, 4, None, 1) == 1
    assert hardware.recommended_processes(64, 128, None, 1) == hardware.MAX_PROCESSES
    report = hardware.report()
    assert report['recommended_processes'] >= 1 and report['physical_cores'] >= 1
