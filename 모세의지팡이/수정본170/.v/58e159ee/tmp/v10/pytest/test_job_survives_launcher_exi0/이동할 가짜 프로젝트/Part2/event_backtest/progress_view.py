class ProgressView:
    def __init__(self):
        self.phase = 'fake replay'
        self.build = self.virtual = {}
        self.replay = {'processed': 0, 'total': 100}
        self.lines = []
        self.warnings = []
    def accept(self, event):
        if event.get('event') == 'FAKE_PROGRESS':
            self.replay = {'processed': event['processed'], 'total': 100}
            self.lines.append('fake progress ' + str(event['processed']))
    def remaining(self):
        return 'fake test only'
