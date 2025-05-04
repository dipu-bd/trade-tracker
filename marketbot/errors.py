class ServiceError(Exception):
    def __init__(self, *args: object) -> None:
        super().__init__(*args)


class IgnorableError(Exception):
    def __init__(self, *args: object) -> None:
        super().__init__(*args)


class AbortedError(Exception):
    def __init__(self, *args: object) -> None:
        super().__init__(*args)
