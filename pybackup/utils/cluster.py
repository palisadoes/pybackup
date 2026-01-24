import socket


def cluster_master(cluster_ip: str | None) -> bool:
    if not cluster_ip:
        return True
    try:
        host_ips = {
            ai[4][0]
            for ai in socket.getaddrinfo(
                socket.gethostname(), None, family=socket.AF_INET
            )
        }
        return cluster_ip in host_ips
    except Exception:
        return False
