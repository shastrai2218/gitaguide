# -*- coding: utf-8 -*-
"""Build the GitaGuide Black Book. Run twice, then measure_pages.ps1, then run again."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib
import front
import ch1
import ch2
import ch3
import ch4
import ch56
import back

lib.setup()

front.cover()
front.certificate()
front.acknowledgement()
front.abstract()
front.toc()
front.lof_lot()
front.abbreviations()

lib.new_section()
ch1.build()
ch2.build()
ch3.build()
ch4.build()
ch56.build5()
ch56.build6()
back.references()
back.appendix_a()
back.appendix_b()
back.appendix_c()
back.appendix_d()

s1, s2 = lib.D.sections[0], lib.D.sections[1]
s1.different_first_page_header_footer = True
s2.different_first_page_header_footer = False
lib.header_footer(s1)
lib.page_numbering(s1, "lowerRoman", 1)
lib.header_footer(s2)
lib.page_numbering(s2, "decimal", 1)
lib.finish(os.path.join(lib.HERE, "Blackbook_GitaGuide.docx"))
print("saved")
