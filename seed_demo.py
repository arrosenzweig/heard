"""Seed clearly-labelled SYNTHETIC demo data for Raleigh, NC.

Every profile and church created here has synthetic=1 and is shown with a
"demo" badge in the interface. None of them is a real person or a real
congregation. Phone numbers use the 555-01xx range, which is reserved for
fiction and does not reach real phones.

Run:  python seed_demo.py          (safe to re-run; ids are fixed)
"""
import json
import store

CITY = "Raleigh, NC"

PEOPLE = [
    dict(id="demo_marcus", handle="Marcus", tradition=["baptist"], overcame=["anger"], fighting=[],
         bio="Eight years ago I put my fist through a wall in front of my son. Haven't raised my voice at him since. Happy to talk at 2am if that's when it hits.",
         open_to_contact=1, contact_method="text", contact_value="(919) 555-0142",
         beliefs={"alcohol": "position_a", "salvation": "position_a", "baptism": "position_b"}),
    dict(id="demo_janelle", handle="Janelle", tradition=["nondenom"], overcame=["lust_purity", "loneliness"], fighting=[],
         bio="Porn had me from 14 to 26. Free four years now. I remember exactly how alone that felt.",
         open_to_contact=1, contact_method="call", contact_value="(919) 555-0167",
         beliefs={"gifts": "position_a", "scripture": "position_a"}),
    dict(id="demo_tom", handle="Tom", tradition=["methodist"], overcame=["addiction"], fighting=["pride"],
         bio="Sober since 2019 through a church recovery group. Still learning that I can't do this alone.",
         open_to_contact=1, contact_method="text", contact_value="(919) 555-0118",
         beliefs={"alcohol": "position_a", "women_leadership": "position_b"}),
    dict(id="demo_grace", handle="Grace", tradition=["catholic"], overcame=["bitterness", "forgiveness"], fighting=["anxiety_fear"],
         bio="Spent ten years unable to forgive my father. Confession and a patient priest got me through.",
         open_to_contact=1, contact_method="text", contact_value="(919) 555-0193",
         beliefs={"communion": "position_a", "baptism": "position_a"}),
    dict(id="demo_dev", handle="Dev", tradition=["exploring"], is_seeker=1, overcame=[], fighting=["doubt", "despair"],
         bio="Not sure what I believe. Grew up with nothing. Showing up anyway.",
         open_to_contact=1, contact_method="text", contact_value="(919) 555-0124", beliefs={}),
    dict(id="demo_ruth", handle="Ruth", tradition=["reformed"], overcame=["greed_money", "work_integrity"], fighting=[],
         bio="I lost a job for refusing to fudge numbers. Best thing that ever happened to me, eventually.",
         open_to_contact=1, contact_method="call", contact_value="(919) 555-0151",
         beliefs={"sovereignty": "position_a", "scripture": "position_a"}),
    dict(id="demo_andre", handle="Andre", tradition=["pentecostal", "baptist"], overcame=[], fighting=["anger", "self_control"],
         bio="Fighting my temper every day and winning more days than I lose. Not further along than you, just walking next to you.",
         open_to_contact=1, contact_method="text", contact_value="(919) 555-0177",
         beliefs={"gifts": "position_a", "alcohol": "position_a"}),
    dict(id="demo_lena", handle="Lena", tradition=["anglican"], overcame=["gossip", "envy"], fighting=[],
         bio="I used to be the one who knew everyone's business. Learned to guard my tongue the slow way.",
         open_to_contact=0, beliefs={}),
]

CHURCHES = [
    dict(id="demo_ch_1", handle="Example Community Church (demo)", tradition=["nondenom"],
         service_times="Sundays 9:30 & 11:15am · Wednesday recovery group 7pm",
         address="(demo listing - not a real address)", website="",
         bio="Demo church profile. Shows how a congregation describes itself and the support groups it runs."),
    dict(id="demo_ch_2", handle="Example Baptist Church (demo)", tradition=["baptist"],
         service_times="Sundays 10:30am · Men's accountability Tuesday 6:30am",
         address="(demo listing - not a real address)", website="",
         bio="Demo church profile. Small-group accountability and a monthly newcomers lunch."),
    dict(id="demo_ch_3", handle="Example Catholic Parish (demo)", tradition=["catholic"],
         service_times="Mass Sat 5pm, Sun 8 & 10:30am · Confession Sat 3:30pm",
         address="(demo listing - not a real address)", website="",
         bio="Demo church profile. Shows Catholic listing fields such as confession times."),
]


def main():
    store.init()
    for p in PEOPLE:
        p = dict(p)
        store.create_profile(kind="person", city=CITY, synthetic=1,
                             is_seeker=p.pop("is_seeker", 0),
                             tradition=json.dumps(p.pop("tradition")), **p)
    for c in CHURCHES:
        c = dict(c)
        store.create_profile(kind="church", city=CITY, synthetic=1,
                             tradition=json.dumps(c.pop("tradition")), **c)
    print(f"seeded {len(PEOPLE)} demo people and {len(CHURCHES)} demo churches in {CITY}")


if __name__ == "__main__":
    main()
