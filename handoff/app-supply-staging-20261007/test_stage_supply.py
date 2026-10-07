import io, json, os
from pathlib import Path
import tempfile, unittest, zipfile
import stage_supply as s

class Staging(unittest.TestCase):
    def tiny(self):
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as z:
            z.writestr('inputs/a.txt',b'fixture')
        buf.seek(0)
        return zipfile.ZipFile(buf)

    def test_success_private_and_existing_stop(self):
        with tempfile.TemporaryDirectory(dir=os.environ['HOME']) as folder, self.tiny() as z:
            fd=os.open(folder,os.O_RDONLY|os.O_DIRECTORY)
            try:
                result=s.stage(z,{},fd,'new',os.getuid())
                self.assertEqual(result['files_verified'],1)
                p=Path(folder)/'new/inputs/a.txt'
                self.assertEqual(p.read_bytes(),b'fixture')
                self.assertEqual(p.stat().st_mode & 0o777,0o600)
                self.assertEqual(p.parent.stat().st_mode & 0o777,0o700)
                with self.assertRaises(FileExistsError): s.stage(z,{},fd,'new',os.getuid())
                self.assertEqual(p.read_bytes(),b'fixture')
            finally: os.close(fd)

    def test_parent_symlink_stop(self):
        with tempfile.TemporaryDirectory(dir=os.environ['HOME']) as folder:
            base=Path(folder); (base/'real').mkdir(mode=0o700)
            (base/'link').symlink_to(base/'real',target_is_directory=True)
            with self.assertRaises(OSError): s.directory(base/'link',os.getuid())

    def test_destination_symlink_stop(self):
        with tempfile.TemporaryDirectory(dir=os.environ['HOME']) as folder, self.tiny() as z:
            base=Path(folder); (base/'other').mkdir(); (base/'new').symlink_to(base/'other',target_is_directory=True)
            fd=os.open(folder,os.O_RDONLY|os.O_DIRECTORY)
            try:
                with self.assertRaises(FileExistsError): s.stage(z,{},fd,'new',os.getuid())
                self.assertEqual(list((base/'other').iterdir()),[])
            finally: os.close(fd)

    def test_partial_failure_has_no_success_receipt(self):
        with tempfile.TemporaryDirectory(dir=os.environ['HOME']) as folder, self.tiny() as z:
            fd=os.open(folder,os.O_RDONLY|os.O_DIRECTORY)
            try:
                with self.assertRaises(ValueError): s.stage(z,{'inputs/a.txt':'0'*64},fd,'new',os.getuid())
                self.assertTrue((Path(folder)/'new/inputs/a.txt').exists())
                self.assertFalse((Path(folder)/'new/STAGED_FILES_RECEIPT.json').exists())
            finally: os.close(fd)

    def test_corrupt_input_stop(self):
        with tempfile.TemporaryFile() as stream:
            stream.write(b'bad'); stream.seek(0)
            with self.assertRaises(ValueError): s.verify(stream)

    def test_unsafe_names_stop(self):
        for name in ('../x','/tmp/x','a//b','a/./b','a\\b','a:b',''):
            with self.subTest(name=name), self.assertRaises(ValueError): s.safe_name(name)

if __name__=='__main__': unittest.main(verbosity=2)
