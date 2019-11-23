import itertools
import numpy as np
from scipy.spatial import Delaunay, ConvexHull
from scipy.optimize import linprog
from .site import Site
from .tools import x_pbc, species_string_from_site

class PolyhedralSite(Site):
    """Describes a site defined by the polyhedral volume enclosed by a set
    of vertex atoms.

    Attributes:
        index (int): Numerical ID, intended to be unique to each site.
        label (`str`: optional): Optional string given as a label for this site.
            Default is `None`.
        contains_atoms (list): List of the atoms contained by this site in the
            structure last processed.
        trajectory (list): List of sites this atom has visited at each timestep?
        points (list): List of fractional coordinates for atoms assigned as
            occupying this site.
        transitions (collections.Counter): Stores observed transitions from this
            site to other sites. Format is {index: count} with ``index`` giving
            the index of each destination site, and ``count`` giving the number 
            of observed transitions to this site.
        vertex_species (list(str)): List of species that define the vertices,
            e.g. ``['S', 'I']``.
        vertex_indices (list(int)): List of integer indices for the vertex atoms
            (counting from 0). 
        label (:obj:`str`, optional): Optional label for the site.
   
    """ 
    def __init__(self, vertex_species, vertex_indices, label=None):
        """Create a PolyhedralSite instance.

        Args:
            vertex_species (str or list(str)): String identifying the vertex species, e.g. ``'S'``,
                or a list of strings, e.g, ``['S', 'I']``..
            vertex_indices (list(int)): List of integer indices for the vertex atoms (counting from 0).
            label (:obj:`str`, optional): Optional label for this site.

        Returns:
            None

        """
        super(PolyhedralSite, self).__init__(label=label)
        if isinstance(vertex_species, str):
            vertex_species = [ vertex_species ]
        self.vertex_species = vertex_species
        self.vertex_indices = vertex_indices
        self.vertex_coords = None
        self._delaunay = None

    def reset(self):
        """Reset the trajectory for this site.

        Resets the contains_atoms and trajectory attributes
        to empty lists.

        Vertex coordinates and Delaunay tesselation are unset.

        Args:
            None

        Returns:
            None

        """
        super(PolyhedralSite, self).reset()
        self.vertex_coords = None
        self._delaunay = None
 
    @property
    def delaunay(self):
        """Delaunay tesselation of the vertex coordinates for this site.

        This is calculated the first time the attribute is requested,
        and then stored for reuse, unless the site is reset.

        Returns:
            (scipy.spatial.Delaunay)

        """
        if not self._delaunay:
            self._delaunay = Delaunay(self.vertex_coords)         
        return self._delaunay

    @property
    def coordination_number(self):
        """Coordination number for this site, defined as the number of 
        vertices

        Returns:
            (int)

        """
        return len(self.vertex_indices)
    
    @property
    def cn(self):
        """Coordination number for this site, defined as the number of
        vertices

        Convenience property for coordination_number()

        Returns:
            (int)

        """
        return self.coordination_number
        
    def assign_vertex_coords(self, structure):
        """Assign fractional coordinates to the polyhedra vertices
        from the corresponding atom positions in a pymatgen Structure.

        Args:
            structure (Structure): The pymatgen Structure used to assign
                the vertices fractional coordinates.

        Returns:
            None

        Notes:
            This method assumes the coordinates of the vertices may 
            have changed, so unsets the Delaunay tesselation for this site.

        """
        frac_coords = np.array([ s.frac_coords for s in 
            [ structure[i] for i in self.vertex_indices ] ] )
        # Handle periodic boundary conditions:
        # If the range of fractional coordinates along x, y, or z 
        # exceeds 0.5, assume that this polyhedron wraps around the 
        # periodic boundary in that dimension. 
        # Fractional coordinates for that dimension that are less 
        # than 0.5 will be incremented by 1.0 
        for i in range(3):
            spread = max(frac_coords[:,i]) - min(frac_coords[:,i])
            if spread > 0.5:
                for j, fc in enumerate(frac_coords):
                    if fc[i] < 0.5:
                        frac_coords[j,i] += 1.0
        self.vertex_coords = frac_coords
        self._delaunay = None

    def get_vertex_species(self, structure):
        return [ structure[i].species_string for i in self.vertex_indices ]

    def contains_point(self, x, structure=None):
        if structure:
            self.assign_vertex_coords(structure)
        if self.vertex_coords is None:
            raise RuntimeError('no vertex coordinates set for polyhedral_site {}'.format(self.index))
        return np.any( self.delaunay.find_simplex(x_pbc(x)) >= 0 )
    
    def contains_point_new(self, x):
        if self.vertex_coords is None:
            raise RuntimeError('no vertex coordinates set for polyhedral_site {}'.format(self.index))
        for p in x_pbc(x):
            if np.any( self.contains_point_alt(p)):
                return True
        return False
 
    def contains_point_alt(self, x):
        """Alternative algorithm for calculating whether a point sits
        inside a convex hull.

        This algorithm is a potential target for future optimisation
   
        """
        hull = ConvexHull(self.vertex_coords)
        faces = hull.points[hull.simplices]
        centre = self.centre()
        dotsum = 0
        for f in faces:
            surface_normal = np.cross(f[0]-f[2], f[1]-f[2])
            c_sign = np.sign(np.dot( surface_normal, centre-f[0]))
            p_sign = np.sign(np.dot( surface_normal, x-f[0]))
            dotsum += c_sign * p_sign
        return dotsum == len(faces)

    def contains_atom(self, atom):
        return self.contains_point(atom.frac_coords)

    def contains_atom_accurate(self, atom):
        return self.contains_point_accurate(atom.frac_coords)

    def contains_atom_new(self, atom):
        return self.contains_point_new(atom.frac_coords)

    def as_dict(self):
        d = super(PolyhedralSite, self).as_dict()
        d['vertex_species'] = self.vertex_species
        d['vertex_indices'] = self.vertex_indices
        d['vertex_coords'] = self.vertex_coords
        return d

    @classmethod
    def from_dict(cls, d):
        polyhedral_site = cls( vertex_species=d['vertex_species'],
                          vertex_indices=d['vertex_indices'] )
        polyhedral_site.vertex_coords = d['vertex_coords']
        polyhedral_site.contains_atoms = d['contains_atoms']
        polyhedral_site.label = d.get('label')
        return polyhedral_site 

    def centre(self):
        return np.mean(self.vertex_coords, axis=0)

def in_hull(points, x):
    n_points = len(points)
    n_dim = len(x)
    c = np.zeros(n_points)
    A = np.r_[points.T,np.ones((1,n_points))]
    b = np.r_[x, np.ones(1)]
    lp = linprog(c, A_eq=A, b_eq=b)
    return lp.success

