"""Convert event timestamps to ``YYYY-MM-DDTHH:MM:SS.SS`` format."""

from pathlib import Path

import pandas as pd


EVENT_FILE = Path(__file__).resolve().parent / "order_management" / "events.csv"
TIMESTAMP_COLUMNS = ("start_time", "end_time")


def convert_event_timestamps(input_file: Path = EVENT_FILE) -> None:
	events = pd.read_csv(input_file)

	for column in TIMESTAMP_COLUMNS:
		if column not in events.columns:
			raise ValueError(f"Missing required column: {column}")

		# The source values are decimal Unix timestamps in seconds.
		timestamps = pd.to_datetime(events[column], unit="s", errors="raise")
		# %f produces microseconds; retain its first two digits for hundredths.
		events[column] = timestamps.dt.strftime("%Y-%m-%dT%H:%M:%S.%f").str[:-4]

	events.to_csv(input_file, index=False)


if __name__ == "__main__":
	convert_event_timestamps()
